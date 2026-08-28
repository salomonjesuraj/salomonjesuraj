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
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from infusion_models.oi_buildup import OIBuildupType
from infusion_models.smc import (
    compute_warning_tags,
    nearest_ob_or_fvg_distance_pct,
    order_flow_divergence,
    structural_invalidation,
)
from infusion_models.trade_blueprint import TradeBlueprint, TradeStructure
from infusion_models.trade_horizon import TradeHorizon

from api.options_analytics import compute_max_pain, compute_oi_support_resistance
from api.routes.market import _fetch_full_option_chain
from api.routes.mtf import _load_bars, compute_mtf
from api.volume_profile import compute_volume_profile_from_bars, detect_macro_accumulation_breakout

Payload = dict[str, Any]

FUTURES_STATE_PREFIX = "infusion:futures:"
SIGNAL_KEY_PREFIX = "infusion:signal:"
FEATURE_KEY_PREFIX = "infusion:feature:"
# Same Redis LIST api/routes/journal.py's own JOURNAL_KEY writes/reads --
# duplicated as a literal here rather than cross-imported, matching this
# module's own established "small constant, not worth a shared import"
# precedent (_compute_risk_reward's docstring above).
JOURNAL_KEY = "infusion:journal:paper_trades"
ACTIVE_JOURNAL_STATUSES = {"PLANNED", "WATCH"}

_IST = ZoneInfo("Asia/Kolkata")

# ── TradeHorizon calibration constants ──────────────────────────────
# Infusion's own calibration, not from a cited source -- same posture
# as every other threshold in this codebase (retest.py's
# RETEST_BAND_ATR, futures.py's OI_BUILDUP_DEADBAND_PCT, etc.).
SCALP_MIN_RVOL = 2.5
SCALP_MAX_WALL_PCT = 1.0
BTST_MIN_WALL_PCT = 1.5
BTST_CUTOFF_HOUR_IST = 14.0

# P0 audit fix (2026-08-28) -- same golden-ratio extension
# api/broker_sync.py's own FIB_EXTENSION_T3 already uses for the Position
# Intelligence Card's T3, duplicated as a literal here rather than
# cross-imported (matches this module's own established "small constant,
# not worth a shared import" precedent above). See
# _t3_fallback_from_risk's docstring for why this module needs its own
# fallback distinct from broker_sync's Donchian-channel one.
FIB_EXTENSION_T3 = 2.618
# "Closing near Day High/Low" -- within this fraction of the day's own
# high-low range from the relevant extreme.
BTST_NEAR_EXTREME_FRACTION = 0.15
# "Hitting intraday ATR limits" proxy: today's high-low range has
# already used up at least this multiple of ATR(14). No separate
# ATR-budget tracker exists in this pipeline yet -- this is the closest
# honest read of "the move has used its legs" available today.
INTRADAY_ATR_RANGE_MULT = 0.8


def _compute_risk_reward(entry: float, invalidation: float, target: float) -> float:
    """Identical formula to scanner/scoring.py's compute_risk_reward --
    small enough (6 lines) that duplicating it verbatim per this
    codebase's established cross-service pattern is simpler than a
    shared-module addition for one trivial function."""
    if target < entry and invalidation > entry:
        risk, reward = invalidation - entry, entry - target
    else:
        risk, reward = entry - invalidation, target - entry
    if risk <= 0 or reward <= 0:
        return 0.0
    return round(reward / risk, 2)


def _t3_fallback_from_risk(
    *, bullish: bool, entry: float, invalidation: float, real_t2: float
) -> float:
    """P0 audit fix (2026-08-28): real T3 derived from the signal's own
    measured risk (entry to stop-loss) when features_snapshot doesn't
    carry a t3_price -- the normal case, since engine.py's signal write
    path only ever populates t3_price for a subset of strategies. The
    previous code silently set target_3_fib = target_2_fib in that case,
    so T2 and T3 rendered as the identical number for most signals.

    This uses entry/stop-loss risk, not broker_sync.py's Donchian-channel
    swing range (_fib_extension_targets there) -- deliberately: this
    function runs before this module's own compute_mtf() call (mtf isn't
    fetched until later in build_trade_blueprint, only when a
    trade_horizon classification needs it), and entry/invalidation are
    already real, already-live scanner values at this point in the
    function, so reusing them needs no extra Redis round-trip and no
    reordering of this function's existing call sequence. Same
    FIB_EXTENSION_T3 = 2.618 golden-ratio convention as broker_sync.py;
    only the measured range differs by design (risk vs. Donchian swing).

    Monotonicity guard, found live during this fix's own verification
    (not something the audit's spec anticipated): real_t2 comes from an
    entirely different formula -- scanner/pine_confidence.py's
    confluence-cluster extension (cluster_center + span * ratio), a
    different anchor AND span than entry-to-stop risk -- so nothing
    guarantees entry + risk*2.618 lands beyond it. A live check against
    a real signal shape (entry 1000, stop 990, real t2_price 1035) had
    this fallback land T3 at 1026.18 -- BEHIND T2, a target ladder that
    goes backward, which is a worse and newly-introduced defect than the
    T2-collapse bug this function replaces. Guarded by construction: if
    the risk-based number doesn't clear real_t2 in the trade's favorable
    direction, project one further risk-multiple past real_t2 instead
    (FIB_EXTENSION_T3 - 1.618 == 1.0, the same T2->T3 fib step
    pine_confidence.py's own cluster-extension mode uses) -- always
    strictly beyond T2, never a number smaller than what a real T3
    should be relative to a real T2.

    Zero (not a fabricated positive number) when risk itself is
    non-positive (invalid/missing entry-SL pair) -- callers fall back to
    real_t2 in that case."""
    risk = (entry - invalidation) if bullish else (invalidation - entry)
    if risk <= 0:
        return real_t2
    candidate = entry + risk * FIB_EXTENSION_T3 if bullish else entry - risk * FIB_EXTENSION_T3
    beyond_t2 = candidate > real_t2 if bullish else candidate < real_t2
    if beyond_t2:
        return candidate
    return (
        real_t2 + risk * (FIB_EXTENSION_T3 - 1.618)
        if bullish
        else real_t2 - risk * (FIB_EXTENSION_T3 - 1.618)
    )


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


def _opt_f(row: Payload, key: str) -> float | None:
    """Same coercion as _f, but None (not 0.0) when genuinely absent --
    for structure fields where 0.0 would misread as a real price level."""
    val = row.get(key)
    if val in (None, "", "None"):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


async def _load_active_position(redis: Any, symbol: str) -> Payload | None:
    """"Terminal Edge & Analyst" sprint (2026-08-27) Fast Exit Logic --
    the most recent still-open (PLANNED/WATCH, not yet CLOSED/BLOCKED)
    real journal entry for this symbol, same status machine The Ledger's
    own journal.py already uses. None (not a fabricated position) when
    nothing real is open for this symbol right now. Scans up to 300 rows
    (journal.py's own MAX_JOURNAL_ROWS) since this list isn't indexed by
    symbol -- fine at this list's real size, not called on a hot path
    (TradeBlueprint is polled per-card at 5s, not per-tick).
    """
    raw_rows = await redis.lrange(JOURNAL_KEY, 0, 299)
    for raw in raw_rows:
        try:
            text = raw.decode() if isinstance(raw, bytes) else raw
            row = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(row, dict)
            and str(row.get("symbol", "")).upper() == symbol
            and row.get("status") in ACTIVE_JOURNAL_STATUSES
        ):
            return row
    return None


def _build_trade_rationale(
    *,
    direction: str,
    trend_text: str,
    last_event_label: str,
    oi_buildup: str,
    structure: TradeStructure,
) -> str:
    """Deterministic sentence template over real computed signals --
    NOT an LLM call (see TradeBlueprint.trade_rationale's own docstring
    for why). Every clause only appears when its own underlying signal
    is real and present; a cold symbol with no structure event and
    neutral OI gets a short, honest sentence instead of a padded-out
    fake-sounding one."""
    clauses: list[str] = []

    bullish = direction == "BULL"
    if last_event_label and (
        ("Bullish" in last_event_label and bullish) or ("Bearish" in last_event_label and not bullish)
    ):
        level_word = "resistance" if bullish else "support"
        level = structure.resistance if bullish else structure.support
        level_text = f" ({level:.2f})" if level is not None else ""
        clauses.append(f"Price broke {last_event_label.split()[-1]} through HTF {level_word}{level_text}")

    oi_word = {
        "LONG_BUILDUP": "Bullish long buildup detected in futures OI",
        "SHORT_COVERING": "Short-covering buildup detected in futures OI",
        "SHORT_BUILDUP": "Bearish short buildup detected in futures OI",
        "LONG_UNWINDING": "Long-unwinding detected in futures OI",
    }.get(oi_buildup)
    if oi_word:
        clauses.append(oi_word)

    clauses.append(f"Structural trend is {trend_text.split(' (')[0].lower()}")

    return ". ".join(clauses) + "." if clauses else "No structural or order-flow evidence yet."


def classify_trade_horizon(
    *,
    has_active_signal: bool,
    direction: str,
    retest_status: str,
    oi_buildup: str,
    wall_distance_pct: float | None,
    rel_vol_20d: float,
    ltp: float,
    day_high: float,
    day_low: float,
    atr_14: float,
    mtf_5m: str | None,
    mtf_15m: str | None,
    mtf_1h: str | None,
    mtf_1d: str | None,
    accumulation_base: bool,
    vah_20d: float | None,
    val_20d: float | None,
    signal_hour_ist: float | None,
) -> TradeHorizon:
    """"Sniper HUD" Phase 1 -- expected holding-period classifier.

    Every input here already exists somewhere in this pipeline except
    two disclosed proxies:
      - "5m/15m breakout" (SCALP/INTRADAY's own spec language) is read
        as "that timeframe's MTF state currently agrees with this
        trade's direction" (mtf_5m/mtf_15m), not a breakout event
        tagged to that specific timeframe -- this codebase's own
        breakout/retest tracking (feature_engine/features/retest.py)
        only operates on 1-minute-bar fractal levels, with no per-
        timeframe origin tag.
      - "Multi-day LONG_BUILDUP" (SWING's own spec language) is read as
        today's single-sweep oi_buildup state (futures_queue.py only
        diffs against the previous ~60s sweep, no multi-day OI history
        exists yet) combined with accumulation_base (Volume Profile's
        own multi-day macro-accumulation flag) as the sustained-
        conviction half of the picture.

    Checked SWING -> BTST -> SCALP -> INTRADAY, most structurally
    significant first; UNCLASSIFIED whenever nothing clears one
    horizon's specific bar cleanly, rather than a forced fifth guess.
    """
    if not has_active_signal:
        return TradeHorizon.UNCLASSIFIED

    bullish = direction == "BULL"

    # ── SWING: multi-day value-area break, buildup, daily alignment ──
    if bullish:
        cleared_value_area = vah_20d is not None and ltp > vah_20d
        buildup_ok = oi_buildup == OIBuildupType.LONG_BUILDUP.value
        daily_aligned = mtf_1d == "BULL"
    else:
        cleared_value_area = val_20d is not None and ltp < val_20d
        buildup_ok = oi_buildup == OIBuildupType.SHORT_BUILDUP.value
        daily_aligned = mtf_1d == "BEAR"
    if accumulation_base and cleared_value_area and buildup_ok and daily_aligned:
        return TradeHorizon.SWING

    # ── BTST: late-session breakout, closing near the extreme, room to the wall ──
    late_session = signal_hour_ist is not None and signal_hour_ist >= BTST_CUTOFF_HOUR_IST
    day_range = day_high - day_low
    near_extreme = False
    if day_range > 0:
        near_extreme = (
            (day_high - ltp) / day_range <= BTST_NEAR_EXTREME_FRACTION
            if bullish
            else (ltp - day_low) / day_range <= BTST_NEAR_EXTREME_FRACTION
        )
    wall_clear = wall_distance_pct is not None and wall_distance_pct > BTST_MIN_WALL_PCT
    if late_session and near_extreme and wall_clear:
        return TradeHorizon.BTST

    # ── SCALP: high RVol, short-covering/unwinding driven, wall is close ──
    scalp_buildup = (
        OIBuildupType.SHORT_COVERING.value if bullish else OIBuildupType.LONG_UNWINDING.value
    )
    wall_near = wall_distance_pct is not None and wall_distance_pct < SCALP_MAX_WALL_PCT
    fast_tf_ok = mtf_5m == ("BULL" if bullish else "BEAR")
    if rel_vol_20d > SCALP_MIN_RVOL and oi_buildup == scalp_buildup and wall_near and fast_tf_ok:
        return TradeHorizon.SCALP

    # ── INTRADAY: breakout in flight, 1H trend + buildup aligned, range has used its ATR legs ──
    breakout_in_flight = retest_status != "NO_BREAKOUT"
    trend_15m_ok = mtf_15m == ("BULL" if bullish else "BEAR")
    trend_1h_ok = mtf_1h == ("BULL" if bullish else "BEAR")
    intraday_buildup_ok = oi_buildup == (
        OIBuildupType.LONG_BUILDUP.value if bullish else OIBuildupType.SHORT_BUILDUP.value
    )
    atr_used = atr_14 > 0 and day_range >= atr_14 * INTRADAY_ATR_RANGE_MULT
    if (
        breakout_in_flight
        and trend_15m_ok
        and trend_1h_ok
        and intraday_buildup_ok
        and atr_used
    ):
        return TradeHorizon.INTRADAY

    return TradeHorizon.UNCLASSIFIED


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
        # write path. P0 audit fix (2026-08-28): when it's missing (the
        # normal case), derive a real, mathematically distinct T3 from
        # the signal's own risk via _t3_fallback_from_risk -- NOT a bare
        # copy of T2, which silently collapsed T2 and T3 to the
        # identical number for most signals. See that function's own
        # docstring.
        target_3_fib = _t3_fallback_from_risk(
            bullish=direction == "BULL",
            entry=entry_price,
            invalidation=invalidation_sl,
            real_t2=target_2_fib,
        )
        try:
            snapshot = json.loads(signal_row.get("features_snapshot") or "{}")
            t3_raw = snapshot.get("t3_price")
            if t3_raw not in (None, "", 0, 0.0):
                target_3_fib = float(t3_raw)
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
        intraday, daily, _nifty_daily = await _load_bars(redis, symbol)
    except Exception:
        intraday = []
        daily = []
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

    # ── Trade horizon (Sniper HUD Phase 1) ──────────────────────────────
    ltp_effective = _f(feature_row, "ltp") or (intraday[-1]["close"] if intraday else 0.0)
    wall_distance_pct: float | None = None
    if oi_hurdle_strike is not None and ltp_effective > 0:
        wall_distance_pct = abs(float(oi_hurdle_strike) - ltp_effective) / ltp_effective * 100

    # 20-day daily Value Area, for SWING's "cleared the value area" test
    # -- deliberately a SEPARATE profile from poc_level/vah_level above
    # (those are the intraday session's own profile); reuses the exact
    # same histogram engine on the daily bars instead.
    vah_20d = val_20d = None
    if daily:
        daily_profile = compute_volume_profile_from_bars(
            [
                {"high": b["high"], "low": b["low"], "close": b["close"], "volume": b["volume"]}
                for b in daily[-20:]
            ]
        )
        if daily_profile.get("available"):
            vah_20d = daily_profile.get("vah")
            val_20d = daily_profile.get("val")

    signal_hour_ist: float | None = None
    if signal_row:
        try:
            created_us = int(signal_row.get("created_at_us") or 0)
            if created_us > 0:
                ts = datetime.fromtimestamp(created_us / 1_000_000, tz=UTC).astimezone(_IST)
                signal_hour_ist = ts.hour + ts.minute / 60.0
        except (TypeError, ValueError):
            pass

    try:
        mtf = await compute_mtf(redis, symbol, store=False)
        mtf_states = {tf: row.get("state") for tf, row in (mtf.get("timeframes") or {}).items()}
    except Exception:
        mtf = {}
        mtf_states = {}

    trade_horizon = classify_trade_horizon(
        has_active_signal=bool(signal_row),
        direction=direction,
        retest_status=retest_status,
        oi_buildup=oi_buildup,
        wall_distance_pct=wall_distance_pct,
        rel_vol_20d=_f(feature_row, "rel_vol_20d"),
        ltp=ltp_effective,
        day_high=_f(feature_row, "day_high"),
        day_low=_f(feature_row, "day_low"),
        atr_14=_f(feature_row, "atr_14"),
        mtf_5m=mtf_states.get("5M"),
        mtf_15m=mtf_states.get("15M"),
        mtf_1h=mtf_states.get("1H"),
        mtf_1d=mtf_states.get("1D"),
        accumulation_base=accumulation_base,
        vah_20d=vah_20d,
        val_20d=val_20d,
        signal_hour_ist=signal_hour_ist,
    )
    if signal_row:
        available.append("trade_horizon")
    else:
        unavailable.append("trade_horizon")

    # ── SMC Inception Conviction Model presentation ─────────────────────
    bearish = direction == "BEAR"
    ob_fvg_distance_pct = nearest_ob_or_fvg_distance_pct(feature_row, bearish)
    liquidity_sweep = feature_row.get("last_liquidity_sweep") or None
    has_order_flow_divergence = order_flow_divergence(feature_row, bearish)
    if ob_fvg_distance_pct is not None:
        available.append("ob_fvg_distance_pct")
    else:
        unavailable.append("ob_fvg_distance_pct")

    # "Probabilistic Grading and Warning Tags" (2026-08-27): honest
    # flags surfaced alongside the setup instead of the hard
    # REJECTED_CHASING_OB rejection this pipeline used to apply at the
    # exact same distance line -- see infusion_models.smc's own
    # docstring. rr is only computable when a real active signal
    # supplied entry/SL/T1 above; None (not a fabricated 0.0) for a
    # cold symbol, so compute_warning_tags simply skips the R:R tag.
    rr = (
        _compute_risk_reward(entry_price, invalidation_sl, target_1_fib) if signal_row else None
    )
    warning_tags = compute_warning_tags(ob_fvg_distance_pct, rr)

    # ── Structural geometry + AI Analyst rationale (2026-08-27) ─────────
    # Every field sourced from something already computed elsewhere --
    # see TradeStructure's own docstring for exactly where each one
    # comes from. Real gaps (no daily bar history, cold symbol never
    # confirmed a swing pivot) surface as None, not a fabricated level.
    donchian = mtf.get("donchian") or {}
    trend_text_raw = str(feature_row.get("trend_text") or "RANGE / UNDEFINED")
    last_event_label = str(feature_row.get("last_event_label") or "")
    structure = TradeStructure(
        support=_opt_f(mtf, "blocker_down_level"),
        resistance=_opt_f(mtf, "blocker_up_level"),
        channel_upper=_opt_f(donchian, "high"),
        channel_lower=_opt_f(donchian, "low"),
        trend=trend_text_raw,
    )
    if structure.support is not None or structure.resistance is not None:
        available.append("structure")
    else:
        unavailable.append("structure")

    trade_rationale = _build_trade_rationale(
        direction=direction,
        trend_text=trend_text_raw,
        last_event_label=last_event_label,
        oi_buildup=oi_buildup,
        structure=structure,
    )

    # ── Fast Exit Logic -- an ACTIVE real journal position for this
    # symbol whose own direction has been invalidated by a real
    # structural break. See infusion_models.smc.structural_invalidation's
    # own docstring for the STRUCTURAL_BREAK vs FAST_EXIT rule -- shared
    # with broker_sync.py's own Reversal & Invalidation Watch rather than
    # a second hand-copy of the identical rule.
    active_position = await _load_active_position(redis, symbol)
    if active_position is not None and ltp_effective > 0:
        warning_tags.extend(
            structural_invalidation(
                bullish="CE" in str(active_position.get("decision", "")),
                ltp=ltp_effective,
                support=structure.support,
                resistance=structure.resistance,
                channel_lower=structure.channel_lower,
                channel_upper=structure.channel_upper,
            )
        )

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
        trade_horizon=trade_horizon.value,
        ob_fvg_distance_pct=ob_fvg_distance_pct,
        liquidity_sweep=liquidity_sweep,
        order_flow_divergence=has_order_flow_divergence,
        warning_tags=warning_tags,
        structure=structure,
        trade_rationale=trade_rationale,
        available_fields=available,
        unavailable_fields=unavailable,
    )
