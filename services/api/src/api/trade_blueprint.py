"""Unified TradeBlueprint builder — mathematical audit follow-up, Task 4
(2026-08-25). Bundles Entry/Invalidation/T1-T3, Retest state,
Accumulation base (Volume Profile POC/VAH/VAL), and OI buildup/walls
into one contract. See infusion_models.trade_blueprint.TradeBlueprint's
own docstring for the field-by-field source list.

Presentation layer only -- every field is read from an existing,
already-computed source. Nothing here recomputes scanner's own
entry/SL/target decision; when no live scanner signal exists for a
symbol (the common case -- most symbols most of the time), those
fields are honestly reported as unavailable (0.0, listed in
unavailable_fields), never a fabricated ad-hoc recomputation standing
in for the scanner's own real decision.
"""

from __future__ import annotations

import json
from typing import Any

from infusion_models.oi_buildup import OIBuildupType
from infusion_models.trade_blueprint import TradeBlueprint

from api.options_analytics import compute_max_pain, compute_oi_support_resistance
from api.routes.market import _fetch_full_option_chain
from api.routes.mtf import _load_bars
from api.volume_profile import compute_volume_profile_from_bars, detect_macro_accumulation_breakout

Payload = dict[str, Any]

FUTURES_STATE_PREFIX = "infusion:futures:"
SIGNAL_KEY_PREFIX = "infusion:signal:"
FEATURE_KEY_PREFIX = "infusion:feature:"


def _decode_hash(raw: Payload) -> Payload:
    out: Payload = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        out[key] = val
    return out


async def _load_active_signal(redis: Any, symbol: str) -> Payload | None:
    """The most recent scanner-published signal for this symbol, if one
    is still within its TTL (infusion:signal:{symbol}, engine.py's own
    "legacy" per-symbol key) -- None (not fabricated levels) once it has
    expired or none has fired recently."""
    raw = await redis.hgetall(f"{SIGNAL_KEY_PREFIX}{symbol}")
    if not raw:
        return None
    return _decode_hash(raw)


async def _load_feature_row(redis: Any, symbol: str) -> Payload:
    raw = await redis.hgetall(f"{FEATURE_KEY_PREFIX}{symbol}")
    return _decode_hash(raw) if raw else {}


async def _load_futures_row(redis: Any, symbol: str) -> Payload:
    raw = await redis.hgetall(f"{FUTURES_STATE_PREFIX}{symbol}")
    return _decode_hash(raw) if raw else {}


def _f(row: Payload, key: str, default: float = 0.0) -> float:
    try:
        val = row.get(key)
        return float(val) if val not in (None, "") else default
    except (TypeError, ValueError):
        return default


async def build_trade_blueprint(redis: Any, symbol: str) -> TradeBlueprint:
    symbol = symbol.upper()
    available: list[str] = []
    unavailable: list[str] = []

    # ── Entry / Invalidation / T1-T3 -- reuse scanner's own real decision ──
    signal_row = await _load_active_signal(redis, symbol)
    if signal_row:
        direction = (
            "BULL" if str(signal_row.get("signal_type", "")).lower() == "bullish" else "BEAR"
        )
        setup_name = str(signal_row.get("strategy_id") or "unknown_strategy")
        entry_price = _f(signal_row, "entry_price")
        invalidation_sl = _f(signal_row, "invalidation_price")
        target_1_fib = _f(signal_row, "target_price")  # T1
        target_2_fib = _f(signal_row, "t2_price")
        # t3_price only lives inside features_snapshot's own JSON blob,
        # not as a top-level hash field -- see engine.py's own signal
        # write path.
        target_3_fib = target_2_fib
        try:
            snapshot = json.loads(signal_row.get("features_snapshot") or "{}")
            target_3_fib = float(snapshot.get("t3_price") or target_2_fib)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        target_method = str(signal_row.get("target_method") or "unavailable")
        available.extend(
            ["entry_price", "invalidation_sl", "target_1_fib", "target_2_fib", "target_3_fib"]
        )
    else:
        direction = "BULL"
        setup_name = "no_active_signal"
        entry_price = invalidation_sl = target_1_fib = target_2_fib = target_3_fib = 0.0
        target_method = "unavailable"
        unavailable.extend(
            ["entry_price", "invalidation_sl", "target_1_fib", "target_2_fib", "target_3_fib"]
        )

    # ── Retest state (§1.2) -- live feature hash ────────────────────────
    feature_row = await _load_feature_row(redis, symbol)
    retest_status = str(feature_row.get("retest_status") or "NO_BREAKOUT")
    retest_level_raw = feature_row.get("retest_level")
    retest_level = float(retest_level_raw) if retest_level_raw not in (None, "", "None") else None
    if feature_row:
        available.append("retest_status")
    else:
        unavailable.append("retest_status")

    # ── Volume Profile POC/VAH/VAL + accumulation base (§1.1) ───────────
    accumulation_base = False
    poc_level = vah_level = val_level = None
    try:
        intraday, _daily, _nifty_daily = await _load_bars(redis, symbol)
    except Exception:
        intraday = []
    if intraday:
        profile = compute_volume_profile_from_bars(
            [
                {"high": b["high"], "low": b["low"], "close": b["close"], "volume": b["volume"]}
                for b in intraday
            ]
        )
        if profile.get("available"):
            poc_level = profile.get("poc")
            vah_level = profile.get("vah")
            val_level = profile.get("val")
            available.extend(["poc_level", "vah_level", "val_level"])
            current_price = _f(feature_row, "ltp") or (intraday[-1]["close"] if intraday else 0.0)
            prev_price = intraday[-2]["close"] if len(intraday) >= 2 else current_price
            rel_vol = _f(feature_row, "rel_vol_20d")
            accumulation_base = detect_macro_accumulation_breakout(
                profile, current_price=current_price, prev_price=prev_price, rel_vol=rel_vol
            )
        else:
            unavailable.extend(["poc_level", "vah_level", "val_level"])
    else:
        unavailable.extend(["poc_level", "vah_level", "val_level"])

    # ── OI buildup (§3.1) -- futures sweep cache ─────────────────────────
    futures_row = await _load_futures_row(redis, symbol)
    oi_buildup = str(futures_row.get("oi_buildup") or OIBuildupType.NEUTRAL.value)
    if futures_row:
        available.append("oi_buildup")
    else:
        unavailable.append("oi_buildup")

    # ── OI attraction (Max Pain) / hurdle (wall in this trade's way) ────
    oi_attraction_strike: float | None = None
    oi_hurdle_strike: float | None = None
    chain = await _fetch_full_option_chain(redis, symbol)
    if chain.get("ready"):
        rows = chain.get("rows") or []
        max_pain = compute_max_pain(rows)
        support_resistance = compute_oi_support_resistance(rows)
        if max_pain:
            oi_attraction_strike = max_pain.get("max_pain_strike")
            available.append("oi_attraction_strike")
        else:
            unavailable.append("oi_attraction_strike")
        if support_resistance:
            # Hurdle = the wall in the way of THIS blueprint's own
            # direction: a BULL setup's hurdle is overhead call-OI
            # resistance; a BEAR setup's hurdle is put-OI support below.
            oi_hurdle_strike = (
                support_resistance.get("resistance")
                if direction == "BULL"
                else support_resistance.get("support")
            )
            available.append("oi_hurdle_strike")
        else:
            unavailable.append("oi_hurdle_strike")
    else:
        unavailable.extend(["oi_attraction_strike", "oi_hurdle_strike"])

    return TradeBlueprint(
        symbol=symbol,
        direction=direction,
        setup_name=setup_name,
        entry_price=entry_price,
        invalidation_sl=invalidation_sl,
        target_1_fib=target_1_fib,
        target_2_fib=target_2_fib,
        target_3_fib=target_3_fib,
        target_method=target_method,
        retest_status=retest_status,
        retest_level=retest_level,
        accumulation_base=accumulation_base,
        poc_level=poc_level,
        vah_level=vah_level,
        val_level=val_level,
        oi_buildup=oi_buildup,
        oi_attraction_strike=oi_attraction_strike,
        oi_hurdle_strike=oi_hurdle_strike,
        available_fields=available,
        unavailable_fields=unavailable,
    )
