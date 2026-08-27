"""Paper-trade journal for Infusion.

The journal captures the exact setup evidence visible on the dashboard before
any live execution is allowed.  It is intentionally paper-only in this phase:
no broker order endpoint is called from here.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]

IST = ZoneInfo("Asia/Kolkata")
JOURNAL_KEY = "infusion:journal:paper_trades"
JOURNAL_SIGNAL_IDS_KEY = "infusion:journal:signal_ids"
MAX_JOURNAL_ROWS = 300


def _now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _num(value: object, default: float = 0.0) -> float:
    try:
        return round(float(cast(Any, value)), 4)
    except (TypeError, ValueError):
        return default


def _text(value: object, default: str = "-") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _compact_list(value: object, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:limit]:
        text = _text(item, "")
        if text:
            out.append(text)
    return out


def _normalise_trade(payload: Payload) -> Payload:
    symbol = _text(payload.get("symbol"), "UNKNOWN").upper()
    decision = _text(payload.get("decision") or payload.get("trade_decision"), "WAIT").upper()
    option_raw = payload.get("selected_option")
    option = cast(Payload, option_raw) if isinstance(option_raw, dict) else {}
    event_calendar_raw = option.get("event_calendar")
    event_calendar = (
        cast(Payload, event_calendar_raw) if isinstance(event_calendar_raw, dict) else {}
    )
    news_edge_raw = payload.get("news_edge")
    news_edge = cast(Payload, news_edge_raw) if isinstance(news_edge_raw, dict) else {}
    risk_raw = payload.get("risk")
    risk = cast(Payload, risk_raw) if isinstance(risk_raw, dict) else {}
    ts_ms = int(time.time() * 1000)

    entry = _num(payload.get("entry") or payload.get("entry_price_hint"))
    stop = _num(payload.get("stop") or payload.get("stop_loss_hint"))
    target1 = _num(payload.get("target1") or payload.get("target_1_hint"))
    target2 = _num(payload.get("target2") or payload.get("target_2_hint"))
    # "Visual Tracking & Lifecycle" sprint (2026-08-27) -- third target
    # level so lifecycle_monitor.py can resolve WIN_T3, matching the
    # same real Fibonacci/structural target_3_fib the rest of this app
    # already surfaces (ActionCard.tsx's own t1/t2/t3 fallback chain).
    target3 = _num(payload.get("target3") or payload.get("target_3_hint"))
    risk_points = abs(entry - stop) if entry and stop else 0.0
    rr1 = (
        round(abs(target1 - entry) / risk_points, 2)
        if entry and stop and target1 and risk_points
        else 0.0
    )

    execution_status = _text(option.get("execution_status"), "WAIT_CONTRACT")
    hard_blockers = _compact_list(option.get("hard_blockers"), 8)
    option_blockers = _compact_list(option.get("blockers"), 8)
    scanner_blockers = _compact_list(payload.get("rejection_reasons"), 8)
    status = "PLANNED"
    if hard_blockers or execution_status == "AVOID_CONTRACT":
        status = "BLOCKED"
    elif "WAIT" in decision or execution_status == "WAIT_CONTRACT":
        status = "WATCH"

    return {
        "id": f"paper_{ts_ms}_{symbol}",
        "created_at_ist": _now_ist(),
        # "Visual Tracking & Lifecycle" sprint (2026-08-27) -- a real
        # Unix-seconds timestamp alongside the display-only IST string
        # above. lifecycle_monitor.py needs a precise, unambiguous
        # instant to walk real 1-min OHLC bars "since entry" from;
        # parsing created_at_ist's "YYYY-MM-DD HH:MM:SS IST" text back
        # into an instant on every sweep cycle would be fragile and
        # slower for no reason when the real epoch is available for free
        # right here at creation time. Journal rows written before this
        # sprint lack this field -- the monitor skips those rather than
        # guessing, an honest, disclosed gap, not silently faked.
        "created_at_epoch": ts_ms // 1000,
        "source": _text(payload.get("source"), "dashboard_manual_paper"),
        "status": status,
        "signal_id": _text(payload.get("signal_id"), ""),
        "discretionary_action": _text(payload.get("discretionary_action"), "TAKEN").upper(),
        "skip_reason": _text(payload.get("skip_reason"), ""),
        "symbol": symbol,
        "sector": _text(payload.get("sector") or payload.get("sector_id")),
        "decision": decision,
        "ltp": _num(payload.get("ltp")),
        "change_pct": _num(payload.get("change_pct")),
        "rel_vol": _num(payload.get("rel_vol")),
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "target3": target3,
        "rr1": rr1,
        # The scanner's own conviction_grade (A+/A/B/C/D, scoring.py's
        # grade_conviction) at the moment this trade was journaled --
        # NOT option.quality_grade below, which grades the OPTION
        # CONTRACT's own liquidity/tradability, a different axis
        # entirely. Absent for rows logged without a real signal behind
        # them (a manual dashboard entry), honestly "-", never guessed.
        "signal_grade": _text(payload.get("signal_grade"), "-"),
        # Filled in by lifecycle_monitor.py once this trade resolves;
        # None until then, same honest-placeholder convention as
        # "outcome" below -- never fabricated ahead of a real bar
        # actually confirming it.
        "duration": None,
        "positive_above": _num(payload.get("positive_above")),
        "negative_below": _num(payload.get("negative_below")),
        "setup_strength": _num(payload.get("setup_strength")),
        "option_readiness": _num(payload.get("option_readiness")),
        "mtf_score": _num(payload.get("mtf_score")),
        "mtf_text": _text(payload.get("mtf_text"), "MTF building"),
        "risk_amount": _num(risk.get("riskAmount") or risk.get("risk_amount")),
        "risk_pct": _num(risk.get("riskPct") or risk.get("risk_pct")),
        "option": {
            "execution_status": execution_status,
            "quality_grade": _text(option.get("quality_grade"), "-"),
            "trade_ready": bool(option.get("trade_ready")),
            "suggested_contract": _text(option.get("suggested_contract") or option.get("contract")),
            "instrument_key": _text(option.get("instrument_key")),
            "premium": _num(option.get("premium")),
            "bid": _num(option.get("bid")),
            "ask": _num(option.get("ask")),
            "entry_fill": _num(
                option.get("entry_fill") or option.get("ask") or option.get("premium")
            ),
            "exit_fill_reference": _num(option.get("exit_fill_reference") or option.get("bid")),
            "spread_pct": _num(option.get("spread_pct")),
            "spread_per_unit": _num(option.get("spread_per_unit")),
            "est_costs_per_unit": _num(option.get("est_costs_per_unit")),
            "oi": _num(option.get("oi")),
            "iv": _num(option.get("iv")),
            "iv_rank": _num(option.get("iv_rank"), -1),
            "iv_history_count": _num(option.get("iv_history_count")),
            "delta": _num(option.get("delta")),
            "delta_used": _num(option.get("delta_used") or option.get("delta")),
            "premium_risk_pct": _num(option.get("premium_risk_pct")),
            "premium_risk": _num(option.get("premium_risk")),
            "option_sl_price": _num(option.get("option_sl_price")),
            "underlying_risk": _num(option.get("underlying_risk")),
            "breakeven_underlying": _num(option.get("breakeven_underlying")),
            "required_move_pct": _num(option.get("required_move_pct")),
            "expected_move_pct": _num(option.get("expected_move_pct")),
            "target_clears_breakeven": bool(option.get("target_clears_breakeven")),
            "liquidity_whitelist_pass": bool(option.get("liquidity_whitelist_pass")),
            "physical_settlement_block": bool(option.get("physical_settlement_block")),
            "event_calendar": event_calendar,
            "next_event_date": _text(
                option.get("next_event_date") or event_calendar.get("next_event_date"), ""
            ),
            "gross_pnl": _num(option.get("gross_pnl")),
            "total_costs": _num(option.get("total_costs")),
            "net_pnl": _num(option.get("net_pnl")),
            "cost_as_pct_of_premium": _num(option.get("cost_as_pct_of_premium")),
            "expiry_days": _num(option.get("expiry_days")),
            "blockers": option_blockers,
            "hard_blockers": hard_blockers,
        },
        "news_edge": {
            "stance": _text(news_edge.get("stance"), "NO_NEWS"),
            "score": _num(news_edge.get("score")),
            "confidence": _text(news_edge.get("confidence"), "LOW"),
            "action": _text(news_edge.get("action"), "Use price confirmation first"),
            "risks": _compact_list(news_edge.get("risks"), 5),
            "boosters": _compact_list(news_edge.get("boosters"), 5),
            "blockers": _compact_list(news_edge.get("blockers"), 5),
        },
        "strength_reasons": _compact_list(payload.get("strength_reasons"), 8),
        "rejection_reasons": scanner_blockers,
        "notes": _text(payload.get("notes"), ""),
        "outcome": None,
    }


async def load_rows(redis: Any, limit: int = 80) -> list[Payload]:
    raw_rows = await redis.lrange(JOURNAL_KEY, 0, max(0, limit - 1))
    rows: list[Payload] = []
    for raw in raw_rows:
        try:
            if isinstance(raw, bytes):
                text = raw.decode()
            elif isinstance(raw, str):
                text = raw
            else:
                continue
            payload = json.loads(text)
            if isinstance(payload, dict):
                rows.append(cast(Payload, payload))
        except Exception:
            continue
    return rows


async def save_rows(redis: Any, rows: list[Payload]) -> None:
    """Rewrites the whole journal list -- the only way to "update one
    row" in a Redis LIST-backed store with no per-row key. Shared by
    the two route handlers below AND, new this sprint,
    lifecycle_monitor.py's own background sweep, so all three writers
    go through one delete+lpush-reversed+ltrim dance instead of three
    copies of it. This is the same best-effort concurrency posture the
    two route handlers already had before this sprint (a second writer
    racing between this function's own load_rows() and this save_rows()
    could clobber the first's update) -- not a new gap introduced here,
    and not worth a distributed lock for a single-operator paper
    journal at this volume."""
    pipe = redis.pipeline()
    pipe.delete(JOURNAL_KEY)
    for row in reversed(rows):
        pipe.lpush(JOURNAL_KEY, json.dumps(row))
    pipe.ltrim(JOURNAL_KEY, 0, MAX_JOURNAL_ROWS - 1)
    await pipe.execute()


@routes.get("/api/journal/trades")
async def get_journal_trades(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    limit = int(request.query.get("limit", "80") or 80)
    limit = max(1, min(limit, MAX_JOURNAL_ROWS))
    rows = await load_rows(redis, limit)
    return web.json_response({"ok": True, "count": len(rows), "trades": rows})


@routes.post("/api/journal/trades")
async def create_journal_trade(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    try:
        payload_raw = await request.json()
    except Exception:
        payload_raw = {}
    payload = cast(Payload, payload_raw) if isinstance(payload_raw, dict) else {}
    trade = _normalise_trade(payload)
    if request.query.get("dry_run") == "1":
        return web.json_response({"ok": True, "dry_run": True, "trade": trade})
    await redis.lpush(JOURNAL_KEY, json.dumps(trade))
    await redis.ltrim(JOURNAL_KEY, 0, MAX_JOURNAL_ROWS - 1)
    return web.json_response({"ok": True, "trade": trade})


@routes.post("/api/journal/signals/auto")
async def auto_log_signal(request: web.Request) -> web.Response:
    """Idempotently auto-log every confirmed signal.

    This endpoint is intentionally Redis-backed in the current architecture so
    scanner publication can record signals without waiting on Postgres.
    """
    redis = request.app["redis"]
    try:
        payload_raw = await request.json()
    except Exception:
        payload_raw = {}
    payload = cast(Payload, payload_raw) if isinstance(payload_raw, dict) else {}
    signal_id = _text(payload.get("signal_id"), "")
    symbol = _text(payload.get("symbol"), "UNKNOWN").upper()
    dedupe_id = (
        signal_id
        or f"{symbol}:{_text(payload.get('strategy_id'), '')}:{_text(payload.get('created_at_us'), '')}"
    )
    if not dedupe_id.strip(":"):
        return web.json_response({"ok": False, "error": "signal_id_required"}, status=400)
    added = await redis.sadd(JOURNAL_SIGNAL_IDS_KEY, dedupe_id)
    await redis.expire(JOURNAL_SIGNAL_IDS_KEY, 86400 * 120)
    if not added:
        rows = await load_rows(redis, MAX_JOURNAL_ROWS)
        existing = next((r for r in rows if r.get("signal_id") == dedupe_id), None)
        return web.json_response({"ok": True, "duplicate": True, "trade": existing})
    payload["signal_id"] = dedupe_id
    payload["source"] = "scanner_auto_signal"
    payload["discretionary_action"] = "NOT_REVIEWED"
    trade = _normalise_trade(payload)
    await redis.lpush(JOURNAL_KEY, json.dumps(trade))
    await redis.ltrim(JOURNAL_KEY, 0, MAX_JOURNAL_ROWS - 1)
    return web.json_response({"ok": True, "trade": trade})


@routes.post("/api/journal/trades/{trade_id}/discretion")
async def update_journal_discretion(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    trade_id = request.match_info.get("trade_id")
    try:
        payload_raw = await request.json()
    except Exception:
        payload_raw = {}
    payload = cast(Payload, payload_raw) if isinstance(payload_raw, dict) else {}
    action = _text(payload.get("discretionary_action"), "").upper()
    if action not in {"TAKEN", "SKIPPED", "NOT_REVIEWED"}:
        return web.json_response({"ok": False, "error": "invalid_discretionary_action"}, status=400)
    rows = await load_rows(redis, MAX_JOURNAL_ROWS)
    updated: Payload | None = None
    next_rows: list[Payload] = []
    for row in rows:
        if row.get("id") == trade_id:
            row["discretionary_action"] = action
            row["skip_reason"] = _text(payload.get("skip_reason"), "")
            row["reviewed_at_ist"] = _now_ist()
            if action == "TAKEN" and row.get("status") == "WATCH":
                row["status"] = "PLANNED"
            updated = row
        next_rows.append(row)
    if not updated:
        return web.json_response({"ok": False, "error": "trade_not_found"}, status=404)
    await save_rows(redis, next_rows)
    return web.json_response({"ok": True, "trade": updated})


@routes.post("/api/journal/trades/{trade_id}/outcome")
async def update_journal_outcome(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    trade_id = request.match_info.get("trade_id")
    try:
        payload_raw = await request.json()
    except Exception:
        payload_raw = {}
    payload = cast(Payload, payload_raw) if isinstance(payload_raw, dict) else {}
    rows = await load_rows(redis, MAX_JOURNAL_ROWS)
    updated: Payload | None = None
    next_rows: list[Payload] = []
    for row in rows:
        if row.get("id") == trade_id:
            row["outcome"] = _text(payload.get("outcome"), "REVIEW").upper()
            row["exit_price"] = _num(payload.get("exit_price"))
            row["outcome_notes"] = _text(payload.get("notes"), "")
            row["closed_at_ist"] = _now_ist()
            row["status"] = "CLOSED"
            updated = row
        next_rows.append(row)
    if not updated:
        return web.json_response({"ok": False, "error": "trade_not_found"}, status=404)
    await save_rows(redis, next_rows)
    return web.json_response({"ok": True, "trade": updated})


@routes.get("/api/journal/stats")
async def get_journal_stats(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    rows = await load_rows(redis, MAX_JOURNAL_ROWS)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    today_rows = [r for r in rows if str(r.get("created_at_ist", "")).startswith(today)]
    closed = [r for r in today_rows if r.get("outcome")]
    wins = [r for r in closed if str(r.get("outcome")).upper() in {"WIN", "TARGET", "T1", "T2"}]
    losses = [r for r in closed if str(r.get("outcome")).upper() in {"LOSS", "STOP", "SL"}]
    risk_planned = round(
        sum(_num(r.get("risk_amount")) for r in today_rows if r.get("status") != "BLOCKED"), 2
    )
    win_rate = round((len(wins) / max(1, len(wins) + len(losses))) * 100, 1) if closed else 0.0
    return web.json_response(
        {
            "ok": True,
            "stats": {
                "today": today,
                "total_today": len(today_rows),
                "watch": len([r for r in today_rows if r.get("status") == "WATCH"]),
                "planned": len([r for r in today_rows if r.get("status") == "PLANNED"]),
                "blocked": len([r for r in today_rows if r.get("status") == "BLOCKED"]),
                "closed": len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": win_rate,
                "risk_planned": risk_planned,
            },
        }
    )


@routes.get("/api/journal/expectancy")
async def get_journal_expectancy(request: web.Request) -> web.Response:
    """Cost-aware paper expectancy from journal rows.

    This is the P1 replacement headline for raw precision.  It is deliberately
    journal-based so TAKEN, SKIPPED and NOT_REVIEWED can be separated.
    """
    redis = request.app["redis"]
    rows = await load_rows(redis, MAX_JOURNAL_ROWS)
    closed = [r for r in rows if r.get("status") == "CLOSED" and r.get("outcome")]
    taken = [r for r in rows if str(r.get("discretionary_action", "")).upper() == "TAKEN"]
    skipped = [r for r in rows if str(r.get("discretionary_action", "")).upper() == "SKIPPED"]
    not_reviewed = [
        r
        for r in rows
        if str(r.get("discretionary_action", "NOT_REVIEWED")).upper() == "NOT_REVIEWED"
    ]

    r_values: list[float] = []
    wins = 0
    losses = 0
    gross_wins = 0.0
    gross_losses = 0.0
    cost_drag = 0.0
    for row in closed:
        risk = max(_num(row.get("risk_amount")), 1.0)
        outcome = str(row.get("outcome") or "").upper()
        rr = _num(row.get("rr1"), 1.0)
        opt_raw = row.get("option")
        opt = cast(Payload, opt_raw) if isinstance(opt_raw, dict) else {}
        costs = _num(opt.get("total_costs"))
        cost_drag += costs
        if outcome in {"WIN", "TARGET", "T1", "T2"}:
            wins += 1
            r = max(rr, 0.1)
            gross_wins += r
            r_values.append(round(r - costs / risk, 3))
        elif outcome in {"LOSS", "STOP", "SL"}:
            losses += 1
            gross_losses += 1.0
            r_values.append(round(-1.0 - costs / risk, 3))

    expectancy = round(sum(r_values) / len(r_values), 3) if r_values else None
    profit_factor = round(gross_wins / gross_losses, 3) if gross_losses else None
    hit_rate = round(wins / max(wins + losses, 1), 3) if wins + losses else None
    max_dd = 0.0
    equity = 0.0
    peak = 0.0
    for r in reversed(r_values):
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    return web.json_response(
        {
            "ok": True,
            "sample": {
                "total": len(rows),
                "closed": len(closed),
                "taken": len(taken),
                "skipped": len(skipped),
                "not_reviewed": len(not_reviewed),
            },
            "expectancy_r": expectancy,
            "profit_factor": profit_factor,
            "hit_rate": hit_rate,
            "cost_drag": round(cost_drag, 2),
            "max_drawdown_r": round(max_dd, 3),
            "r_values": r_values[-80:],
            "note": "Post-v5.2 journal expectancy; precision is secondary.",
        }
    )
