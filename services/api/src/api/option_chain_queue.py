"""Smart Upstox option-chain refresh queue.

This worker keeps contract-level option readiness fresh for the candidates that
matter most: active signals, pre-breakout leaders, and high-momentum F&O rows.
It intentionally avoids refreshing every symbol every cycle because option-chain
REST calls are heavier and broker rate limits can make the dashboard sluggish.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime
from typing import Any

import msgpack
import structlog

from api.routes.market import _feature, _signal, _upstox_option_context

logger = structlog.get_logger()

STATUS_KEY = "infusion:option-chain-queue:status"
REFRESH_LOCK_PREFIX = "infusion:option-chain-refresh-lock:"
LAST_REFRESH_PREFIX = "infusion:option-chain-last-refresh:"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _decode_hash(data: dict | None) -> dict:
    if not data:
        return {}
    out: dict[str, Any] = {}
    for key, val in data.items():
        k = key.decode() if isinstance(key, bytes) else key
        v = val.decode() if isinstance(val, bytes) else val
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            out[k] = v
    return out


def _infer_bias(signal: dict, features: dict) -> str:
    signal_bias = str(signal.get("option_bias") or signal.get("bias") or "").upper()
    if "CE" in signal_bias:
        return "CE"
    if "PE" in signal_bias:
        return "PE"
    change_pct = _num(features.get("change_pct"))
    if change_pct >= 0.15:
        return "CE"
    if change_pct <= -0.15:
        return "PE"
    return "WAIT"


def _candidate_score(features: dict, prebreak: dict, signal_score: float = 0.0) -> float:
    change_pct = abs(_num(features.get("change_pct")))
    rel_vol = _num(features.get("rel_vol_20d") or features.get("rel_vol"))
    rsi = _num(features.get("rsi_14"), 50.0)
    bb_width = _num(features.get("bb_width"))
    pre_score = _num(prebreak.get("readiness_score") or prebreak.get("ready"))
    score = 0.0
    score += min(26.0, rel_vol * 8.0)
    score += min(20.0, change_pct * 5.0)
    score += 16.0 if 38 <= rsi <= 68 else 8.0 if 32 <= rsi <= 74 else 0.0
    score += 12.0 if bb_width and bb_width >= 0.002 else 0.0
    score += min(22.0, pre_score * 0.22)
    score += min(30.0, signal_score * 0.30)
    return round(score, 2)


async def _symbol_universe(redis) -> list[str]:
    raw = await redis.hgetall("infusion:symbols")
    symbols: list[str] = []
    for item in raw.values():
        try:
            meta = msgpack.unpackb(item, raw=False) if isinstance(item, bytes) else item
            symbol = str(meta.get("symbol") or "").upper().strip()
            if symbol and meta.get("segment") != "INDEX":
                membership = meta.get("index_membership") or []
                if not membership or "FNO" in membership:
                    symbols.append(symbol)
        except Exception:
            continue
    return sorted(set(symbols))


async def build_candidates(redis, limit: int) -> list[dict]:
    """Return ranked symbols for option-chain confirmation."""
    ranked: dict[str, dict] = {}

    active = await redis.zrevrange("infusion:signals:active", 0, max(limit - 1, 0), withscores=True)
    for member, score in active:
        text = member.decode() if isinstance(member, bytes) else str(member)
        symbol = text.split(":")[0].upper().strip()
        if symbol:
            ranked[symbol] = {"symbol": symbol, "source": "active_signal", "signal_score": float(score or 0)}

    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="infusion:prebreak:*", count=300)
        if not keys and cursor == 0:
            break
        pipe = redis.pipeline(transaction=False)
        for key in keys:
            pipe.hgetall(key)
        rows = await pipe.execute() if keys else []
        for key, data in zip(keys, rows):
            name = (key.decode() if isinstance(key, bytes) else str(key)).replace("infusion:prebreak:", "")
            pre = _decode_hash(data)
            score = _num(pre.get("readiness_score") or pre.get("ready"))
            if score >= 55:
                row = ranked.setdefault(name.upper(), {"symbol": name.upper(), "source": "prebreak", "signal_score": 0.0})
                row["prebreak_score"] = max(_num(row.get("prebreak_score")), score)
                row["source"] = f"{row.get('source','')},prebreak".strip(",")
        if cursor == 0:
            break

    symbols = await _symbol_universe(redis)
    pipe = redis.pipeline(transaction=False)
    for symbol in symbols:
        pipe.hgetall(f"infusion:feature:{symbol}")
        pipe.hgetall(f"infusion:prebreak:{symbol}")
    results = await pipe.execute()
    feature_rows: list[tuple[str, dict, dict]] = []
    for idx, symbol in enumerate(symbols):
        features = _decode_hash(results[idx * 2])
        prebreak = _decode_hash(results[idx * 2 + 1])
        if features:
            feature_rows.append((symbol, features, prebreak))

    for symbol, features, prebreak in feature_rows:
        existing = ranked.setdefault(symbol, {"symbol": symbol, "source": "feature_momentum", "signal_score": 0.0})
        score = _candidate_score(features, prebreak, _num(existing.get("signal_score")))
        if score >= 48 or existing.get("source") != "feature_momentum":
            existing["rank_score"] = max(_num(existing.get("rank_score")), score)
            existing["source"] = existing["source"] if existing["source"] != "feature_momentum" else "feature_momentum"
        elif existing.get("source") == "feature_momentum":
            ranked.pop(symbol, None)

    candidates = sorted(
        ranked.values(),
        key=lambda x: (_num(x.get("signal_score")) > 0, _num(x.get("rank_score")) + _num(x.get("prebreak_score"))),
        reverse=True,
    )
    return candidates[: max(0, limit)]


async def refresh_candidates_once(redis, *, limit: int = 28, delay_ms: int = 350) -> dict:
    candidates = await build_candidates(redis, limit)
    refreshed: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    started = datetime.now().isoformat(timespec="seconds")

    for row in candidates:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        lock_key = f"{REFRESH_LOCK_PREFIX}{symbol}"
        locked = await redis.set(lock_key, "1", ex=75, nx=True)
        if not locked:
            skipped.append(symbol)
            continue
        try:
            features = await _feature(redis, symbol)
            signal = await _signal(redis, symbol)
            bias = _infer_bias(signal, features)
            if bias not in {"CE", "PE"}:
                skipped.append(symbol)
                continue
            result = await _upstox_option_context(redis, symbol, bias, features, signal)
            await redis.set(
                f"{LAST_REFRESH_PREFIX}{symbol}",
                json.dumps({
                    "symbol": symbol,
                    "bias": bias,
                    "ready": bool(result.get("ready")),
                    "execution_status": result.get("execution_status") or "CHAIN_PENDING",
                    "score": result.get("execution_score") or result.get("option_score"),
                    "raw_score": result.get("raw_option_score"),
                    "reason": result.get("reason") or result.get("score_cap_detail") or "",
                    "refreshed_at": datetime.now().isoformat(timespec="seconds"),
                    "source": row.get("source"),
                }, separators=(",", ":")),
                ex=600,
            )
            refreshed.append(symbol)
        except Exception as exc:
            failed.append({"symbol": symbol, "error": str(exc)[:180]})
            logger.warning("option_chain_queue_refresh_failed", symbol=symbol, error=str(exc))
        finally:
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)

    status = {
        "enabled": True,
        "started_at": started,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_limit": limit,
        "candidate_count": len(candidates),
        "refreshed_count": len(refreshed),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "refreshed": refreshed[:40],
        "skipped": skipped[:40],
        "failed": failed[:20],
    }
    await redis.set(STATUS_KEY, json.dumps(status, separators=(",", ":")), ex=600)
    return status


async def option_chain_queue_loop(app) -> None:
    config = app["config"]
    redis = app["redis"]
    enabled = bool(getattr(config, "option_chain_auto_refresh_enabled", True))
    interval = max(20, int(getattr(config, "option_chain_refresh_interval_sec", 45) or 45))
    limit = max(0, int(getattr(config, "option_chain_candidate_limit", 28) or 28))
    delay_ms = max(0, int(getattr(config, "option_chain_request_delay_ms", 350) or 350))
    if not enabled or limit <= 0:
        await redis.set(STATUS_KEY, json.dumps({"enabled": False, "reason": "disabled_by_config"}), ex=600)
        return
    logger.info("option_chain_queue_started", interval=interval, limit=limit, delay_ms=delay_ms)
    while True:
        with contextlib.suppress(Exception):
            status = await refresh_candidates_once(redis, limit=limit, delay_ms=delay_ms)
            logger.info("option_chain_queue_cycle", **{k: v for k, v in status.items() if not isinstance(v, list)})
        await asyncio.sleep(interval)
