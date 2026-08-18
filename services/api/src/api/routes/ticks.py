"""Ticks route — latest tick data per symbol from hot state.

Endpoints:
  GET /api/ticks             → bulk tick data for all tracked symbols
  GET /api/ticks/{symbol}    → single symbol tick data
  GET /api/ticks/snapshot    → full tick + feature snapshot (for dashboard init)
  GET /api/symbols           → loaded symbol universe metadata

Designed for 500-symbol production load:
  - Uses Redis pipeline for bulk reads
  - Reads symbol list from infusion:symbols (not KEYS/SCAN)
"""

import json
from datetime import date

import msgpack
from aiohttp import web
import structlog

from api.event_calendar import EVENT_KEY_PREFIX
from api.intelligence import build_intelligence_layer

routes = web.RouteTableDef()
logger = structlog.get_logger()

TRADE_HORIZON_LABELS = {
    "INTRADAY": "Intraday",
    "BTST_1_2D": "Short Term",
    "SWING": "Medium Term",
    "AVOID": "Avoid",
}


def _potential_upside_pct(decision: str, ltp: float, target_1: float) -> float:
    """Broker-card "X% Potential Upside" figure -- measured off current
    price (LTP), matching how a research-call card frames it: what's
    achievable from where price is right now, not from a pending entry
    trigger. For BUY PE (bearish), "upside" means the expected downward
    move toward target, framed as a positive percentage."""
    if ltp <= 0:
        return 0.0
    if decision == "BUY PE":
        return round((ltp - target_1) / ltp * 100, 2)
    return round((target_1 - ltp) / ltp * 100, 2)


def _trade_horizon_label(trade_horizon: str) -> str:
    """Friendly Short/Medium-Term display label over the existing
    trade_horizon classification (INTRADAY/BTST_1_2D/SWING/AVOID) --
    matches the vocabulary of a broker research-call card without
    introducing a second, parallel holding-period classifier."""
    return TRADE_HORIZON_LABELS.get(trade_horizon, trade_horizon.title())
NEWS_EDGE_KEY_PREFIX = "infusion:news-edge:"


def _classify_oi_buildup(oi_change_pct, price_change_pct) -> dict:
    """Standard 4-way OI interpretation: does open interest agree with price
    direction (fresh positioning) or oppose it (positions closing)?

      price up   + OI up   -> Long Buildup    (fresh longs, bullish)
      price down + OI up   -> Short Buildup   (fresh shorts, bearish)
      price up   + OI down -> Short Covering  (shorts closing, bullish)
      price down + OI down -> Long Unwinding  (longs closing, bearish)

    Was in the original design (docs/PHASE-3-SCANNER-CONVICTION-ENGINE.md
    section 9.2) but never actually implemented -- oi_change_pct was already
    being computed per-contract (market.py's _upstox_option_context) and
    used only as a pass/fail liquidity gate, never classified or surfaced.
    Only meaningful once OI has moved enough to mean something -- a +/-3%
    band is treated as flat/neutral, not a signal.
    """
    try:
        oi_chg = float(oi_change_pct)
        px_chg = float(price_change_pct)
    except (TypeError, ValueError):
        return {"label": "NO_DATA", "bias": "NEUTRAL"}

    FLAT_BAND = 3.0
    if abs(oi_chg) < FLAT_BAND:
        return {"label": "OI FLAT", "bias": "NEUTRAL"}

    oi_up = oi_chg > 0
    price_up = px_chg > 0
    if oi_up and price_up:
        return {"label": "LONG BUILDUP", "bias": "BULLISH"}
    if oi_up and not price_up:
        return {"label": "SHORT BUILDUP", "bias": "BEARISH"}
    if not oi_up and price_up:
        return {"label": "SHORT COVERING", "bias": "BULLISH"}
    return {"label": "LONG UNWINDING", "bias": "BEARISH"}


def _index_snapshot_from_hash(symbol: str, label: str, data: dict) -> dict:
    snap = {"symbol": symbol, "label": label, "available": False}
    if not data:
        return snap
    decoded = _decode_hash(data)
    snap.update({
        "available": True,
        "ltp": decoded.get("ltp"),
        "change_pct": decoded.get("change_pct"),
        "updated_at": decoded.get("updated_at"),
    })
    return snap


async def _scanner_index_context(redis) -> dict:
    """Read live index context from hot Redis ticks for every scanner row."""
    pipe = redis.pipeline()
    pipe.hgetall("infusion:tick:NIFTY50")
    pipe.hgetall("infusion:tick:NIFTY")
    pipe.hgetall("infusion:tick:NIFTYBANK")
    pipe.hgetall("infusion:tick:BANKNIFTY")
    pipe.hgetall("infusion:tick:GIFTNIFTY")
    n50, nifty, nb, bank, gift = await pipe.execute()
    nifty_data = n50 or nifty or {}
    bank_data = nb or bank or {}
    gift_data = gift or {}
    nifty_snap = _index_snapshot_from_hash("NIFTY50", "NIFTY", nifty_data)
    bank_snap = _index_snapshot_from_hash("NIFTYBANK", "BANKNIFTY", bank_data)
    gift_snap = _index_snapshot_from_hash("GIFTNIFTY", "GIFT NIFTY", gift_data)
    n_chg = float(nifty_snap.get("change_pct") or 0)
    b_chg = float(bank_snap.get("change_pct") or 0)
    if nifty_snap.get("available") and bank_snap.get("available"):
        avg = (n_chg + b_chg) / 2.0
    elif nifty_snap.get("available"):
        avg = n_chg
    elif bank_snap.get("available"):
        avg = b_chg
    else:
        avg = 0.0
    regime = "BULLISH" if avg >= 0.35 else "BEARISH" if avg <= -0.35 else "NEUTRAL"
    return {
        "nifty": nifty_snap,
        "banknifty": bank_snap,
        "gift_nifty": gift_snap,
        "market_index_change_pct": round(avg, 3),
        "market_index_regime": regime,
    }


def _apply_index_context(entry: dict, context: dict) -> None:
    nifty = context.get("nifty") or {}
    bank = context.get("banknifty") or {}
    gift = context.get("gift_nifty") or {}
    entry["nifty_ltp"] = nifty.get("ltp")
    entry["nifty_change_pct"] = nifty.get("change_pct")
    entry["banknifty_ltp"] = bank.get("ltp")
    entry["banknifty_change_pct"] = bank.get("change_pct")
    entry["gift_nifty_ltp"] = gift.get("ltp")
    entry["gift_nifty_change_pct"] = gift.get("change_pct")
    entry["market_index_change_pct"] = context.get("market_index_change_pct")
    entry["market_index_regime"] = context.get("market_index_regime")


async def _fo_ban_context(redis) -> dict:
    """F&O ban list (Phase 13.13) for the scanner table -- same source
    scanner/suppression.py's real Gate 1 reads (infusion:nse:fo_ban:symbols,
    captured by nse_scraper/fo_ban.py from NSE's daily MWPL>=95% list), just
    fetched ONCE per /api/ticks request (one SMEMBERS) rather than a
    per-symbol SISMEMBER call -- the set is small (single digits to a few
    dozen symbols on a real trading day), so one bulk read plus a local
    Python `in` check per row is both correct and far cheaper than 200+
    Redis round trips.
    """
    pipe = redis.pipeline()
    pipe.smembers("infusion:nse:fo_ban:symbols")
    pipe.get("infusion:nse:fo_ban:trade_date")
    symbols_raw, trade_date_raw = await pipe.execute()
    symbols = set()
    for s in (symbols_raw or []):
        symbols.add(s.decode() if isinstance(s, bytes) else s)
    trade_date = trade_date_raw.decode() if isinstance(trade_date_raw, bytes) else trade_date_raw
    return {"symbols": symbols, "trade_date": trade_date}


def _apply_fo_ban_context(entry: dict, symbol: str, context: dict) -> None:
    entry["fo_banned"] = symbol in context.get("symbols", set())
    entry["fo_ban_trade_date"] = context.get("trade_date")


async def _vwap_state_context(redis, symbols: list[str]) -> dict:
    """Phase R2 -- previous poll's VWAP state per symbol, needed to tell a
    genuine reclaim (was BELOW, now ABOVE) apart from a sustained
    continuation (was already ABOVE). One bulk MGET across every symbol
    per request, not a per-row round trip -- same shape as
    _fo_ban_context's one-SMEMBERS-not-208-SISMEMBERs reasoning above.
    """
    if not symbols:
        return {}
    keys = [f"infusion:vwap-state:{s}" for s in symbols]
    values = await redis.mget(keys)
    result = {}
    for symbol, raw in zip(symbols, values):
        if raw:
            result[symbol] = raw.decode() if isinstance(raw, bytes) else raw
    return result


async def _write_vwap_state_context(redis, updates: dict) -> None:
    """Batched write-back of this request's VWAP states, for the *next*
    request to diff against -- one pipeline for up to 208 symbols, not
    208 round trips. Every key carries its own EXPIRE: this is a small
    transition-detection cache, not meant to grow into a permanent,
    ever-accumulating Redis surface across 208 symbols x every trading
    day. 6h comfortably spans a full session (09:15-15:30 IST) plus
    normal poll-gap slack; a symbol that stops updating (feed gap,
    delisting) ages out on its own rather than lingering.
    """
    if not updates:
        return
    pipe = redis.pipeline(transaction=False)
    for symbol, state in updates.items():
        pipe.set(f"infusion:vwap-state:{symbol}", state, ex=6 * 3600)
    await pipe.execute()


def _classify_breakout_type(entry: dict, prev_vwap_state: str | None) -> str | None:
    """Phase R2 -- one breakout-type label per row, priority-ordered
    (most specific / most transitional wins). Every input is already on
    `entry` by the time this runs (from _scanner_intel, plus the
    vwap-state context passed in) -- pure classification over evidence
    that already exists, no new computation.
    """
    vwap_state = entry.get("vwap_state")
    day_high = entry.get("day_high")
    day_low = entry.get("day_low")
    ltp = float(entry.get("ltp") or 0)
    rel_vol = float(entry.get("rel_vol") or 0)
    anti_chase_ok = bool(entry.get("anti_chase_ok"))
    chase_quality = entry.get("chase_quality")

    # 1. VWAP reclaim/rejection -- a genuine transition, not a snapshot.
    if prev_vwap_state == "BELOW" and vwap_state == "ABOVE":
        return "vwap_reclaim"
    if prev_vwap_state == "ABOVE" and vwap_state == "BELOW":
        return "vwap_rejection"

    # 2. Fresh day-high/day-low break -- direction-aware, mirrors R1's
    # price-location component. The reference plan names only "Day High
    # Break"; "Day Low Break" is the natural symmetric extension for the
    # bearish (BUY PE) side this whole system already treats
    # symmetrically everywhere else.
    if day_high and ltp >= day_high:
        return "day_high_break"
    if day_low and ltp <= day_low:
        return "day_low_break"

    # 3. Sustained continuation on the correct side of VWAP -- gated on
    # at least modest elevated volume (1.3x, softer than volume_surge's
    # 2.0x) so "happens to be above VWAP with nothing else going on"
    # (true for roughly half the universe on any given day) doesn't get
    # labeled as if it were active breakout evidence.
    if vwap_state == "ABOVE" and rel_vol >= 1.3:
        return "above_vwap_continuation"
    if vwap_state == "BELOW" and rel_vol >= 1.3:
        return "below_vwap_continuation"

    # 4. Volume alone, nothing else above qualified.
    if rel_vol >= 2.0:
        return "volume_surge"

    # 5. Explicitly a bad chase -- worth a label, not just silence.
    if not anti_chase_ok or chase_quality == "NO_CHASE":
        return "failed_no_chase"

    return None


def _decode_hash(data: dict) -> dict:
    result = {}
    for k, v in data.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        try:
            result[key] = float(val)
        except (ValueError, TypeError):
            result[key] = val
    return result


def _score_color(score: float) -> str:
    if score >= 80:
        return "green"
    if score >= 55:
        return "yellow"
    return "red"


def _mtf_state(score: float) -> str:
    if score >= 58:
        return "BULL"
    if score <= 42:
        return "BEAR"
    return "MIXED"


def _mtf_dot(state: str) -> str:
    return {"BULL": "G", "BEAR": "R", "MIXED": "Y"}.get(state, "Y")


def _options_level_plan(
    *,
    bullish: bool,
    entry: float,
    ltp: float,
    atr: float,
    day_high: float,
    day_low: float,
    fibo_r2: float,
    fibo_s2: float,
    option_score: float,
    anti_chase: bool,
) -> dict:
    """Build realistic stock-option underlying levels.

    A one-rupee target can look valid mathematically, but it is usually not
    tradable for stock options after premium spread, theta, and slippage. This
    planner therefore floors the expected move using ATR + percentage move +
    intraday structure, then derives R:R from that practical move.
    """
    if entry <= 0:
        return {
            "stop": 0.0,
            "target1": 0.0,
            "target2": 0.0,
            "risk": 0.0,
            "rr1": 0.0,
            "method": "No valid entry",
        }

    day_range = max(day_high - day_low, 0.0) if day_high > 0 and day_low > 0 else 0.0
    # For options, target should normally need at least ~0.55-0.65% underlying
    # follow-through; otherwise the option premium may not pay enough.
    atr_floor = max(float(atr or 0.0), entry * 0.0045, day_range * 0.18, 0.50)
    stop_dist = max(atr_floor * (0.85 if anti_chase else 0.75), entry * 0.0035, 0.50)
    rr_floor = 1.8 if option_score >= 75 else 1.6 if option_score >= 60 else 1.45
    t1_dist = max(stop_dist * rr_floor, atr_floor * 1.25, entry * 0.0060, 1.00)
    t2_dist = max(stop_dist * 3.0, atr_floor * 2.20, entry * 0.0110, 1.50)
    # T3 "runner" target — same escalation pattern as T1/T2, matches the
    # ratio scanner/pine_confidence.py's practical_option_targets() uses.
    t3_dist = max(stop_dist * 4.5, atr_floor * 3.20, entry * 0.0160, 2.00)

    if bullish:
        structure_t1 = max(x for x in [entry + t1_dist, fibo_r2 if fibo_r2 > entry else 0.0, day_high if day_high > entry else 0.0])
        target1 = structure_t1
        target2 = max(entry + t2_dist, target1 + max(stop_dist, atr_floor * 0.75))
        target3 = max(entry + t3_dist, target2 + max(stop_dist, atr_floor * 0.75))
        stop = entry - stop_dist
    else:
        structure_t1 = min(x for x in [entry - t1_dist, fibo_s2 if 0 < fibo_s2 < entry else entry - t1_dist, day_low if 0 < day_low < entry else entry - t1_dist])
        target1 = structure_t1
        target2 = min(entry - t2_dist, target1 - max(stop_dist, atr_floor * 0.75))
        target3 = min(entry - t3_dist, target2 - max(stop_dist, atr_floor * 0.75))
        stop = entry + stop_dist

    risk = abs(entry - stop)
    reward = abs(target1 - entry)
    rr1 = round(reward / risk, 2) if risk > 0 and reward > 0 else 0.0
    return {
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "target3": target3,
        "risk": risk,
        "rr1": rr1,
        "method": (
            f"ATR/structure plan: risk {risk:.2f}, T1 move {reward:.2f}, "
            f"R:R {rr1:.2f}; floored for option spread/theta"
        ),
    }


def _side_trade_map(
    *,
    side: str,
    entry: float,
    invalidation: float,
    sustain_rule: str,
    source: str,
    reason: str,
    atr: float = 0.0,
) -> dict:
    """Create a standalone CE/PE underlying map without borrowing the other side's levels."""
    side = str(side or "WATCH").upper()
    entry = float(entry or 0.0)
    invalidation = float(invalidation or 0.0)
    atr_safe = max(float(atr or 0.0), entry * 0.004 if entry else 0.0, 0.50)
    risk = abs(entry - invalidation) if entry and invalidation else 0.0
    target_1_dist = max(risk * 1.65, atr_safe * 1.35, entry * 0.006 if entry else 0.0, 1.00)
    target_2_dist = max(risk * 2.80, atr_safe * 2.25, entry * 0.011 if entry else 0.0, 1.50)

    if side == "BUY CE":
        target_1 = entry + target_1_dist
        target_2 = entry + target_2_dist
        trigger_text = f"Turns bullish above {entry:.2f}"
        invalid_text = f"Invalid below {invalidation:.2f}"
    elif side == "BUY PE":
        target_1 = entry - target_1_dist
        target_2 = entry - target_2_dist
        trigger_text = f"Turns bearish below {entry:.2f}"
        invalid_text = f"Invalid above {invalidation:.2f}"
    else:
        target_1 = 0.0
        target_2 = 0.0
        trigger_text = "No directional entry"
        invalid_text = "Wait for CE/PE confirmation"

    rr1 = round(target_1_dist / risk, 2) if risk > 0 else 0.0
    return {
        "side": side,
        "entry": round(entry, 2),
        "stop_loss": round(invalidation, 2),
        "target_1": round(target_1, 2),
        "target_2": round(target_2, 2),
        "risk_points": round(risk, 2),
        "rr1": rr1,
        "sustain_rule": str(sustain_rule or "5M/15M close"),
        "source": str(source or "scanner"),
        "trigger_text": trigger_text,
        "invalid_text": invalid_text,
        "reason": str(reason or ""),
    }


def _scanner_intel(entry: dict, features: dict, prebreak: dict | None = None, sector_strength: float = 50.0) -> dict:
    """Compute compact scanner intelligence from currently available intraday features.

    This is intentionally deterministic and lightweight. Deeper 1D/1H trend can be
    layered onto the same fields once those historical features are available.
    """
    ltp = float(entry.get("ltp") or features.get("ltp") or 0)
    change_pct = float(entry.get("change_pct") or features.get("change_pct") or 0)
    vwap = float(features.get("vwap") or 0)
    ema5 = float(features.get("ema_5") or 0)
    ema9 = float(features.get("ema_9") or 0)
    ema20 = float(features.get("ema_20") or 0)
    ema50 = float(features.get("ema_50") or 0)
    rsi = float(features.get("rsi_14") or 50)
    macd = float(features.get("macd") or 0)
    macd_signal = float(features.get("macd_signal") or 0)
    macd_hist = float(features.get("macd_hist") or 0)
    rel_vol = float(features.get("rel_vol_20d") or entry.get("rel_vol") or 0)
    bb_width = float(features.get("bb_width") or 0)
    spread = float(features.get("spread_bps") or 999)
    atr_trend = str(features.get("atr_trend") or "NEUTRAL").upper()
    atr_trail_stop = float(features.get("atr_trail_stop") or 0)
    squeeze_state = str(features.get("squeeze_state") or "NA").upper()
    nr_pattern = str(features.get("nr_pattern") or "")
    candle_pattern = str(features.get("candle_pattern") or "")
    atr = float(features.get("atr_14") or 0)
    prev_close = float(features.get("prev_close") or entry.get("prev_close") or 0)
    day_high = float(features.get("day_high") or entry.get("day_high") or ltp or 0)
    day_low = float(features.get("day_low") or entry.get("day_low") or ltp or 0)

    above_vwap = ltp > vwap > 0
    below_vwap = vwap > ltp > 0
    ema_bull = ltp > ema9 > ema20 > 0
    ema_bear = ltp < ema9 < ema20 and ema20 > 0
    ema_stack_bull = ltp > ema5 > ema9 > ema20 > 0
    ema_stack_bear = ltp < ema5 < ema9 < ema20 and ema20 > 0
    macd_bull = macd > macd_signal and macd_hist > 0
    macd_bear = macd < macd_signal and macd_hist < 0
    atr_bull = atr_trend == "BULL" and (atr_trail_stop <= 0 or ltp > atr_trail_stop)
    atr_bear = atr_trend == "BEAR" and (atr_trail_stop <= 0 or ltp < atr_trail_stop)
    pk_squeeze = squeeze_state in {"EXTREME", "COILED", "BUILDING"} or nr_pattern in {"NR4", "NR7"}
    bull_candle = candle_pattern in {"Bullish Engulfing", "Hammer"}
    bear_candle = candle_pattern in {"Bearish Engulfing", "Shooting Star"}
    atr_safe = atr if atr > 0 else max(ltp * 0.004, 0.05)
    vwap_distance_atr = abs(ltp - vwap) / atr_safe if vwap > 0 else 9.99
    signal_candle_atr = abs(change_pct) / 100.0 * ltp / atr_safe if atr_safe > 0 else 0.0

    bull_points = 0.0
    bear_points = 0.0
    bull_points += 18 if above_vwap else 0
    bear_points += 18 if below_vwap else 0
    bull_points += 18 if ema_bull else 8 if ltp > ema9 > 0 else 0
    bear_points += 18 if ema_bear else 8 if ltp < ema9 and ema9 > 0 else 0
    bull_points += 12 if ema_stack_bull else 0
    bear_points += 12 if ema_stack_bear else 0
    bull_points += 14 if macd_bull else 0
    bear_points += 14 if macd_bear else 0
    bull_points += 10 if atr_bull else 0
    bear_points += 10 if atr_bear else 0
    bull_points += 12 if 50 <= rsi <= 68 else 6 if 45 <= rsi < 50 else 0
    bear_points += 12 if 32 <= rsi <= 50 else 6 if 50 < rsi <= 55 else 0
    bull_points += 8 if change_pct > 0 else 0
    bear_points += 8 if change_pct < 0 else 0
    bull_points += 4 if bull_candle else 0
    bear_points += 4 if bear_candle else 0

    if bull_points - bear_points >= 14:
        trend_bias = "BUY"
        trend_score = min(100.0, 50 + bull_points - bear_points)
    elif bear_points - bull_points >= 14:
        trend_bias = "SELL"
        trend_score = min(100.0, 50 + bear_points - bull_points)
    else:
        trend_bias = "HOLD"
        trend_score = max(35.0, 55 - abs(bull_points - bear_points))

    compression = 0.0
    if bb_width > 0:
        compression = min(max((1 - bb_width / 0.012) * 100, 0), 100)

    setup_state = str((prebreak or {}).get("state") or "").upper()
    setup_score = float((prebreak or {}).get("readiness_score") or 0)
    if setup_score <= 0:
        setup_score = (
            min(rel_vol / 3.0, 1.0) * 24
            + min(compression / 100.0, 1.0) * 18
            + (10 if pk_squeeze else 0)
            + (18 if above_vwap or below_vwap else 0)
            + (14 if 42 <= rsi <= 68 else 5)
            + min(max(sector_strength, 0), 100) * 0.20
        )
    setup_score = round(min(setup_score, 100), 1)

    liquidity_ok = spread < 35
    option_score = (
        trend_score * 0.34
        + setup_score * 0.30
        + min(rel_vol / 3.0, 1.0) * 18
        + (10 if liquidity_ok else 3 if spread < 70 else 0)
        + min(max(sector_strength, 0), 100) * 0.08
    )
    option_score = round(min(max(option_score, 0), 100), 1)

    if trend_bias == "BUY" and option_score >= 70:
        decision = "BUY CE"
    elif trend_bias == "SELL" and option_score >= 70:
        decision = "BUY PE"
    elif option_score >= 55 or setup_score >= 70:
        decision = "HOLD"
    else:
        decision = "AVOID"

    mtf_scores = {
        "1M": 50 + (12 if above_vwap else -12 if below_vwap else 0) + (12 if macd_bull else -12 if macd_bear else 0) + (rsi - 50) * 0.7,
        "5M": 50 + (16 if ema_bull else -16 if ema_bear else 0) + (10 if macd_bull else -10 if macd_bear else 0),
        "15M": 50 + (18 if ema_stack_bull else -18 if ema_stack_bear else 0) + (8 if atr_bull else -8 if atr_bear else 0),
        "1H": 50 + (18 if ltp > ema50 > 0 else -18 if ltp < ema50 and ema50 > 0 else 0) + (8 if change_pct > 0 else -8 if change_pct < 0 else 0),
        "4H": 50 + (15 if ltp > ema50 > 0 and ema20 > ema50 > 0 else -15 if ltp < ema50 and ema20 < ema50 and ema50 > 0 else 0),
        "1D": 50 + (14 if change_pct > 0 else -14 if change_pct < 0 else 0) + (8 if above_vwap else -8 if below_vwap else 0),
    }
    mtf = {tf: _mtf_state(score) for tf, score in mtf_scores.items()}
    mtf_dots = {tf: _mtf_dot(state) for tf, state in mtf.items()}
    bull_mtf = sum(1 for state in mtf.values() if state == "BULL")
    bear_mtf = sum(1 for state in mtf.values() if state == "BEAR")
    if bull_mtf >= 5:
        mtf_text = "Strong CE alignment"
    elif bear_mtf >= 5:
        mtf_text = "Strong PE alignment"
    elif bull_mtf >= 4:
        mtf_text = "CE focus; wait trigger"
    elif bear_mtf >= 4:
        mtf_text = "PE focus; wait trigger"
    elif mtf["1M"] == mtf["5M"] and mtf["15M"] != mtf["1H"]:
        mtf_text = "Fast scalp only"
    else:
        mtf_text = "Mixed alignment"

    anti_chase_reasons = []
    if vwap_distance_atr > 1.5:
        anti_chase_reasons.append(f"VWAP stretch {vwap_distance_atr:.1f} ATR")
    if signal_candle_atr > 2.0:
        anti_chase_reasons.append(f"Large candle {signal_candle_atr:.1f} ATR")
    if decision == "BUY CE" and rsi > 75:
        anti_chase_reasons.append(f"CE chase RSI {rsi:.1f}")
    if decision == "BUY PE" and rsi < 25:
        anti_chase_reasons.append(f"PE chase RSI {rsi:.1f}")

    pivot_base = prev_close if prev_close > 0 else ltp
    hl_range = max(day_high - day_low, atr_safe * 2, ltp * 0.003)
    fibo_pivot = (day_high + day_low + pivot_base) / 3 if day_high > 0 and day_low > 0 else pivot_base
    fibo_s2 = fibo_pivot - 0.618 * hl_range
    fibo_r2 = fibo_pivot + 0.618 * hl_range
    ce_trigger_candidates = [x for x in [vwap, ema20, atr_trail_stop, fibo_pivot, ltp + atr_safe * 0.18] if x and x > 0]
    pe_trigger_candidates = [x for x in [vwap, ema20, atr_trail_stop, fibo_pivot, ltp - atr_safe * 0.18] if x and x > 0]
    positive_above = max(ce_trigger_candidates) if ce_trigger_candidates else ltp + atr_safe * 0.25
    negative_below = min(pe_trigger_candidates) if pe_trigger_candidates else ltp - atr_safe * 0.25
    anti_chase_for_levels = bool(anti_chase_reasons)
    if decision == "BUY PE":
        entry_hint = negative_below
        level_plan = _options_level_plan(
            bullish=False,
            entry=entry_hint,
            ltp=ltp,
            atr=atr_safe,
            day_high=day_high,
            day_low=day_low,
            fibo_r2=fibo_r2,
            fibo_s2=fibo_s2,
            option_score=option_score,
            anti_chase=anti_chase_for_levels,
        )
        stop_hint = level_plan["stop"]
        target_1 = level_plan["target1"]
        target_2 = level_plan["target2"]
        target_3 = level_plan["target3"]
        sr_status = "Below support trigger" if ltp < negative_below else "Watching breakdown"
        setup_type = "PE breakdown/rejection"
    else:
        entry_hint = positive_above
        level_plan = _options_level_plan(
            bullish=True,
            entry=entry_hint,
            ltp=ltp,
            atr=atr_safe,
            day_high=day_high,
            day_low=day_low,
            fibo_r2=fibo_r2,
            fibo_s2=fibo_s2,
            option_score=option_score,
            anti_chase=anti_chase_for_levels,
        )
        stop_hint = level_plan["stop"]
        target_1 = level_plan["target1"]
        target_2 = level_plan["target2"]
        target_3 = level_plan["target3"]
        sr_status = "Above resistance trigger" if ltp > positive_above else "Watching breakout"
        setup_type = "CE breakout/reclaim" if decision == "BUY CE" else "Neutral trigger watch"
    points_diff = (ltp - prev_close) if prev_close > 0 else 0.0
    pct_diff = (points_diff / prev_close * 100) if prev_close > 0 else change_pct
    if decision in {"BUY CE", "BUY PE"} and not anti_chase_reasons:
        status = "ARMED"
    elif decision in {"BUY CE", "BUY PE"}:
        status = "WAIT_RETEST"
    elif option_score >= 55 or setup_score >= 60:
        status = "WATCH"
    else:
        status = "NO_TRADE"
    if bull_mtf >= 4 and trend_bias == "BUY":
        alignment = "CE aligned"
    elif bear_mtf >= 4 and trend_bias == "SELL":
        alignment = "PE aligned"
    else:
        alignment = "Mixed"

    strength_reasons: list[str] = []
    weakness_reasons: list[str] = []

    is_sell_bias = trend_bias == "SELL"
    is_buy_bias = trend_bias == "BUY"

    if above_vwap and not is_sell_bias:
        strength_reasons.append("Price above VWAP")
    elif below_vwap and is_sell_bias:
        strength_reasons.append("Price below VWAP for PE")
    elif below_vwap:
        weakness_reasons.append("Price below VWAP")
    elif above_vwap and is_sell_bias:
        weakness_reasons.append("Price above VWAP against PE")
    else:
        weakness_reasons.append("VWAP not ready")

    if ema_stack_bull and not is_sell_bias:
        strength_reasons.append("EMA 5/9/20 bullish stack")
    elif ema_stack_bear and is_sell_bias:
        strength_reasons.append("EMA 5/9/20 bearish stack")
    elif ema_stack_bear:
        weakness_reasons.append("EMA stack bearish")
    elif ema_bull:
        strength_reasons.append("Price holding EMA9/20")
    elif ema_bear:
        weakness_reasons.append("Price below EMA9/20")
    else:
        weakness_reasons.append("EMA trend mixed")

    if macd_bull and not is_sell_bias:
        strength_reasons.append("MACD bullish momentum")
    elif macd_bear and is_sell_bias:
        strength_reasons.append("MACD bearish momentum")
    elif macd_bear:
        weakness_reasons.append("MACD bearish")
    else:
        weakness_reasons.append("MACD flat")

    if 50 <= rsi <= 68 and not is_sell_bias:
        strength_reasons.append(f"RSI healthy bullish zone {rsi:.1f}")
    elif 32 <= rsi <= 50 and is_sell_bias:
        strength_reasons.append(f"RSI supports bearish setup {rsi:.1f}")
    elif 32 <= rsi <= 50:
        weakness_reasons.append(f"RSI bearish zone {rsi:.1f}")
    elif rsi > 72:
        weakness_reasons.append(f"RSI overheated {rsi:.1f}")
    elif rsi < 28:
        weakness_reasons.append(f"RSI oversold {rsi:.1f}")
    else:
        weakness_reasons.append(f"RSI neutral {rsi:.1f}")

    if rel_vol >= 3:
        strength_reasons.append(f"Volume expansion {rel_vol:.1f}x")
    elif rel_vol >= 1.5:
        strength_reasons.append(f"Volume improving {rel_vol:.1f}x")
    else:
        weakness_reasons.append(f"Volume light {rel_vol:.1f}x")

    if compression >= 70:
        strength_reasons.append(f"Strong compression {compression:.0f}%")
    elif compression >= 45:
        strength_reasons.append(f"Compression building {compression:.0f}%")
    else:
        weakness_reasons.append(f"Compression weak {compression:.0f}%")

    if atr_bull and not is_sell_bias:
        strength_reasons.append("ATR trail bullish")
    elif atr_bear and is_sell_bias:
        strength_reasons.append("ATR trail bearish")
    elif atr_trend in {"BULL", "BEAR"}:
        weakness_reasons.append(f"ATR trail {atr_trend.lower()} against bias")

    if nr_pattern:
        strength_reasons.append(f"{nr_pattern} narrow-range setup")
    if squeeze_state in {"EXTREME", "COILED", "BUILDING"}:
        strength_reasons.append(f"Squeeze {squeeze_state.lower()}")
    if candle_pattern:
        if (is_buy_bias and bull_candle) or (is_sell_bias and bear_candle):
            strength_reasons.append(candle_pattern)
        elif candle_pattern not in {"Doji", "Inside Bar"}:
            weakness_reasons.append(f"{candle_pattern} against bias")

    if sector_strength >= 65:
        strength_reasons.append(f"Sector strong {sector_strength:.0f}")
    elif sector_strength <= 35:
        weakness_reasons.append(f"Sector weak {sector_strength:.0f}")
    else:
        weakness_reasons.append(f"Sector neutral {sector_strength:.0f}")

    if liquidity_ok:
        strength_reasons.append("Spread option-friendly")
    elif spread < 70:
        weakness_reasons.append("Spread acceptable but not ideal")
    else:
        weakness_reasons.append("Spread wide")

    if anti_chase_reasons:
        weakness_reasons.extend(anti_chase_reasons[:2])
    else:
        strength_reasons.append("Anti-chase clean")

    conviction_label = "A+" if option_score >= 85 else "A" if option_score >= 75 else "B" if option_score >= 60 else "C" if option_score >= 45 else "D"
    conviction_zone = "STRONG" if option_score >= 75 else "WATCH" if option_score >= 60 else "WAIT" if option_score >= 45 else "AVOID"
    bull_confidence = round(min(100.0, max(0.0, 20.0 + bull_points * 0.85)), 1)
    bear_confidence = round(min(100.0, max(0.0, 20.0 + bear_points * 0.85)), 1)
    fast_mtf_aligned = (bull_mtf >= 4 and trend_bias == "BUY") or (bear_mtf >= 4 and trend_bias == "SELL")
    higher_mtf_bias = 0
    higher_mtf_bias += 1 if mtf.get("1H") == "BULL" else -1 if mtf.get("1H") == "BEAR" else 0
    higher_mtf_bias += 1 if mtf.get("4H") == "BULL" else -1 if mtf.get("4H") == "BEAR" else 0
    higher_mtf_bias += 1 if mtf.get("1D") == "BULL" else -1 if mtf.get("1D") == "BEAR" else 0
    directional_higher = higher_mtf_bias >= 2 if trend_bias == "BUY" else higher_mtf_bias <= -2 if trend_bias == "SELL" else False
    intraday_score = round(min(100.0, max(0.0, option_score * 0.42 + trend_score * 0.24 + setup_score * 0.20 + min(rel_vol / 3.0, 1.0) * 14)), 1)
    swing_score = round(min(100.0, max(0.0, option_score * 0.24 + trend_score * 0.18 + setup_score * 0.18 + (sector_strength or 0) * 0.14 + (18 if directional_higher else 0) + (8 if compression >= 45 else 0))), 1)
    btst_score = round(min(100.0, max(0.0, swing_score * 0.52 + intraday_score * 0.28 + (12 if fast_mtf_aligned else 0) + (8 if rel_vol >= 1.5 else 0))), 1)
    carry_risk = "HIGH"
    if swing_score >= 78 and directional_higher and not anti_chase_reasons:
        carry_risk = "LOW"
    elif swing_score >= 62 and directional_higher:
        carry_risk = "MEDIUM"

    if anti_chase_reasons:
        chase_quality = "WAIT_RETEST"
    elif option_score >= 78 and rel_vol >= 1.5 and fast_mtf_aligned:
        chase_quality = "HIGHLY_CHASEABLE"
    elif option_score >= 65 and (fast_mtf_aligned or directional_higher):
        chase_quality = "CLEAN"
    elif option_score >= 55:
        chase_quality = "WATCH_ONLY"
    else:
        chase_quality = "NO_CHASE"

    if swing_score >= 72 and directional_higher and carry_risk != "HIGH":
        trade_horizon = "SWING"
        sustain_tf = "15M + 1H close"
        horizon_reason = "Higher timeframes and sector support carry potential."
    elif btst_score >= 70 and directional_higher:
        trade_horizon = "BTST_1_2D"
        sustain_tf = "15M close"
        horizon_reason = "Intraday move has enough 1H/4H support for 1-2 day watch."
    elif intraday_score >= 62:
        trade_horizon = "INTRADAY"
        sustain_tf = "5M or 15M close"
        horizon_reason = "Fast setup; manage same-day unless higher timeframe improves."
    else:
        trade_horizon = "AVOID"
        sustain_tf = "No entry"
        horizon_reason = "Alignment/volume/contract readiness not sufficient."

    # ── Stock Breakout Score (Phase R1) ─────────────────────────
    # Ranks the UNDERLYING's own breakout evidence, independent of option-
    # chain readiness -- see docs/stock-options-dashboard-review-and-
    # structure-plan.md. Every input below is already computed above in
    # this same function; no new I/O. Honest 0-90 scale: the reference
    # plan's remaining 10 points (sector/index relative strength) aren't
    # computed yet (needs new opening-range + index-RS infra, deferred),
    # so this is never silently padded up to look like a completed
    # 100-point model -- see STOCK_BREAKOUT_SCORE_MAX below.
    volume_profile_ready = str(features.get("volume_profile_ready") or "") == "True"
    # rel_vol reads as a bare 0.0 when the symbol's 20-session volume
    # profile hasn't bootstrapped (feature_engine/features/volume.py) --
    # that's "unknown", not "genuinely quiet", so this component is
    # explicitly zeroed (not just naturally low) until the profile is
    # ready, rather than letting a missing baseline masquerade as real
    # evidence of low volume.
    rvol_component = (min(rel_vol / 3.0, 1.0) * 25.0) if volume_profile_ready else 0.0
    day_range = max(day_high - day_low, 0.0)
    if is_sell_bias:
        at_fresh_extreme = day_low > 0 and ltp <= day_low
        dist_pct = (ltp - day_low) / day_range if day_range > 0 else 1.0
    else:
        at_fresh_extreme = day_high > 0 and ltp >= day_high
        dist_pct = (day_high - ltp) / day_range if day_range > 0 else 1.0
    price_location_component = (max(0.0, 1.0 - dist_pct) * 20.0) if day_range > 0 else 0.0
    fresh_extreme_component = 15.0 if at_fresh_extreme else 0.0
    if (above_vwap and ema_stack_bull) or (below_vwap and ema_stack_bear):
        vwap_ema_component = 15.0
    elif (above_vwap and ema_bull) or (below_vwap and ema_bear):
        vwap_ema_component = 10.0
    else:
        vwap_ema_component = 0.0
    mtf_alignment_component = (max(bull_mtf, bear_mtf) / 6.0) * 10.0
    no_chase_component = 5.0 if not anti_chase_reasons else 0.0
    stock_breakout_score = round(
        rvol_component + price_location_component + fresh_extreme_component
        + vwap_ema_component + mtf_alignment_component + no_chase_component,
        1,
    )
    STOCK_BREAKOUT_SCORE_MAX = 90.0
    # Reference plan's thresholds (>=70/100 BREAKOUT_NOW, >=55/100 shows
    # at all) reinterpreted proportionally against the real 90-point max.
    if stock_breakout_score >= 63.0:
        stock_breakout_tier = (
            "BREAKOUT_NOW"
            if (not anti_chase_reasons and chase_quality in {"HIGHLY_CHASEABLE", "CLEAN"})
            else "RETEST_ENTRY"
        )
    elif stock_breakout_score >= 49.5:
        stock_breakout_tier = "EARLY_WATCH"
    else:
        stock_breakout_tier = "NO_CHASE"
    # OPTION_READY (needs real chain data, not available inside this pure
    # function) is applied as a tier upgrade later in list_ticks(), once
    # option_summary has been merged into the row.

    # Broker-style trade-card fields -- see _potential_upside_pct/
    # _trade_horizon_label docstrings for why downside% isn't included
    # symmetrically (stop_hint is entry-relative, not LTP-relative, so it
    # can't be framed the same unambiguous way upside can).
    potential_upside_pct = _potential_upside_pct(decision, ltp, target_1)
    trade_horizon_label = _trade_horizon_label(trade_horizon)

    if decision == "BUY PE":
        breakout_area = negative_below
        invalidation_area = positive_above
        move_to = target_1
        extended_move_to = target_2
        breakdown_to = target_2
        breakout_explanation = (
            f"If {sustain_tf} sustains below {negative_below:.2f}, PE breakdown can move toward "
            f"{target_1:.2f}, then {target_2:.2f}. Above {positive_above:.2f}, bearish view weakens."
        )
    elif decision == "BUY CE":
        breakout_area = positive_above
        invalidation_area = negative_below
        move_to = target_1
        extended_move_to = target_2
        breakdown_to = negative_below
        breakout_explanation = (
            f"If {sustain_tf} sustains above {positive_above:.2f}, CE breakout can move toward "
            f"{target_1:.2f}, then {target_2:.2f}. Below {negative_below:.2f}, bullish view weakens."
        )
    else:
        breakout_area = positive_above
        invalidation_area = negative_below
        move_to = target_1
        extended_move_to = target_2
        breakdown_to = negative_below
        breakout_explanation = (
            f"Watch {positive_above:.2f} for bullish acceptance and {negative_below:.2f} for bearish failure. "
            "No chase until direction and MTF improve."
        )

    if decision == "BUY CE":
        primary_trade_map = _side_trade_map(
            side="BUY CE",
            entry=positive_above,
            invalidation=negative_below,
            sustain_rule=sustain_tf,
            source="scanner_primary",
            reason=breakout_explanation,
            atr=atr_safe,
        )
    elif decision == "BUY PE":
        primary_trade_map = _side_trade_map(
            side="BUY PE",
            entry=negative_below,
            invalidation=positive_above,
            sustain_rule=sustain_tf,
            source="scanner_primary",
            reason=breakout_explanation,
            atr=atr_safe,
        )
    else:
        primary_trade_map = {
            "side": decision,
            "bull_above": round(positive_above, 2),
            "bear_below": round(negative_below, 2),
            "entry": round(entry_hint, 2),
            "stop_loss": round(stop_hint, 2),
            "target_1": round(target_1, 2),
            "target_2": round(target_2, 2),
            "sustain_rule": sustain_tf,
            "source": "scanner_primary_watch",
            "reason": breakout_explanation,
        }

    return {
        "trend_bias": trend_bias,
        "trend_score": round(trend_score, 1),
        "setup_strength": setup_score,
        "setup_state": setup_state,
        "conviction_score": option_score,
        "conviction_label": conviction_label,
        "conviction_zone": conviction_zone,
        "option_readiness": option_score,
        "trade_decision": decision,
        "prev_close": round(prev_close, 2) if prev_close > 0 else None,
        "points_diff": round(points_diff, 2),
        "pct_diff": round(pct_diff, 2),
        "positive_above": round(positive_above, 2),
        "negative_below": round(negative_below, 2),
        "entry_price_hint": round(entry_hint, 2),
        "stop_loss_hint": round(stop_hint, 2),
        "target_1_hint": round(target_1, 2),
        "target_2_hint": round(target_2, 2),
        "target_3_hint": round(target_3, 2),
        "underlying_risk_points": round(float(level_plan.get("risk") or abs(entry_hint - stop_hint)), 2),
        "risk_reward_ratio_hint": round(float(level_plan.get("rr1") or 0), 2),
        "target_method": str(level_plan.get("method") or ""),
        "intraday_score": intraday_score,
        "swing_score": swing_score,
        "btst_score": btst_score,
        "trade_horizon": trade_horizon,
        "trade_horizon_label": trade_horizon_label,
        "horizon_reason": horizon_reason,
        "potential_upside_pct": potential_upside_pct,
        "chase_quality": chase_quality,
        "carry_risk": carry_risk,
        "sustain_rule": sustain_tf,
        "breakout_area": round(breakout_area, 2),
        "invalidation_area": round(invalidation_area, 2),
        "move_to": round(move_to, 2),
        "extended_move_to": round(extended_move_to, 2),
        "breakdown_to": round(breakdown_to, 2),
        "breakout_explanation": breakout_explanation,
        "primary_trade_map": primary_trade_map,
        "mtf_conflict": False,
        "mtf_conflict_note": "",
        "mtf_alternate_trade_map": None,
        "fibo_pivot": round(fibo_pivot, 2),
        "fibo_s2": round(fibo_s2, 2),
        "fibo_r2": round(fibo_r2, 2),
        "sr_status": sr_status,
        "alignment": alignment,
        "status": status,
        "type": setup_type,
        "time_label": "LIVE",
        "level_source": "phase1_live_proxy",
        "pine_confidence": option_score,
        "bull_confidence": bull_confidence,
        "bear_confidence": bear_confidence,
        "mtf": mtf,
        "mtf_dots": mtf_dots,
        "mtf_text": mtf_text,
        "anti_chase_ok": not anti_chase_reasons,
        "anti_chase_reasons": anti_chase_reasons,
        "rejection_reasons": anti_chase_reasons,
        "vwap_distance_atr": round(vwap_distance_atr, 2),
        "signal_candle_atr": round(signal_candle_atr, 2),
        "score_color": _score_color(option_score),
        "vwap_state": "ABOVE" if above_vwap else "BELOW" if below_vwap else "NA",
        "ema_state": "BULL" if ema_bull else "BEAR" if ema_bear else "MIXED",
        "macd_state": "BULL" if macd_bull else "BEAR" if macd_bear else "FLAT",
        "atr_state": atr_trend,
        "atr_trail_stop": round(atr_trail_stop, 2),
        "squeeze_state": squeeze_state,
        "nr_pattern": nr_pattern,
        "candle_pattern": candle_pattern,
        "rsi_state": "BULL" if rsi >= 55 else "BEAR" if rsi <= 45 else "NEUTRAL",
        "rsi_14": round(rsi, 2),
        "rel_vol": round(rel_vol, 2),
        "bb_compression": round(compression, 1),
        "sector_strength": round(float(sector_strength or 0), 1),
        "trend_stack": {
            "vwap": "PASS" if above_vwap or below_vwap else "NA",
            "ema": "BULL" if ema_stack_bull else "BEAR" if ema_stack_bear else "MIXED",
            "macd": "BULL" if macd_bull else "BEAR" if macd_bear else "FLAT",
            "atr": "BULL" if atr_bull else "BEAR" if atr_bear else "NEUTRAL",
            "pattern": "PASS" if pk_squeeze or bull_candle or bear_candle else "WAIT",
            "rsi": "PASS" if (32 <= rsi <= 68) else "WARN",
            "volume": "PASS" if rel_vol >= 1.5 else "WARN",
            "compression": "PASS" if compression >= 45 else "WARN",
            "sector": "PASS" if sector_strength >= 55 else "WARN",
        },
        "strength_reasons": strength_reasons[:6],
        "weakness_reasons": weakness_reasons[:5],
        # Phase R1 -- Stock Breakout Radar (docs/stock-options-dashboard-
        # review-and-structure-plan.md). Ranks the underlying's own
        # breakout evidence, independent of option-chain readiness.
        "stock_breakout_score": stock_breakout_score,
        "stock_breakout_score_max": STOCK_BREAKOUT_SCORE_MAX,
        "stock_breakout_tier": stock_breakout_tier,
        "volume_profile_ready": volume_profile_ready,
        "day_high": round(day_high, 2) if day_high > 0 else None,
        "day_low": round(day_low, 2) if day_low > 0 else None,
    }


def _decode_mtf_cache(raw) -> dict:
    if not raw:
        return {}
    try:
        value = raw.decode() if isinstance(raw, bytes) else raw
        payload = json.loads(value)
        if not isinstance(payload, dict):
            return {}
        return {
            "mtf": payload.get("mtf") or {},
            "mtf_dots": payload.get("mtf_dots") or payload.get("dots") or {},
            "mtf_text": payload.get("mtf_text") or payload.get("alignment") or "Historical MTF ready",
            "mtf_score": payload.get("score"),
            "mtf_source": payload.get("source") or "historical",
            "mtf_timeframes": payload.get("timeframes") or {},
            "mtf_bull_count": payload.get("bull_count"),
            "mtf_bear_count": payload.get("bear_count"),
            "mtf_updated_at": payload.get("updated_at"),
            "mtf_warnings": payload.get("warnings") or [],
            "mtf_rejection_reasons": payload.get("rejection_reasons") or [],
            "mtf_trade_bias": payload.get("trade_bias"),
            # Phase 13.5 -- 52-week high/low distance, see
            # api/routes/mtf.py's _week52_stats().
            "week52": payload.get("week52") or {},
            # Phase 13.12 -- VCP / Minervini Stage-2 composite, see
            # api/vcp.py. Same cached mtf payload, just passed through
            # whole rather than field-by-field like week52 above, since the
            # scanner table wants the full component breakdown for a
            # tooltip, not just the headline score.
            "vcp": payload.get("vcp") or {},
            # Chartink-comparison filter, see api/daily_trend_filter.py --
            # same passthrough-from-cached-mtf-payload pattern as vcp above.
            "daily_trend": payload.get("daily_trend") or {},
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _decode_news_cache(raw) -> dict:
    if not raw:
        return {}
    try:
        value = raw.decode() if isinstance(raw, bytes) else raw
        payload = json.loads(value)
        if not isinstance(payload, dict):
            return {}
        edge = payload.get("edge")
        if isinstance(edge, dict):
            edge = dict(edge)
            edge["cached_at_ist"] = payload.get("cached_at_ist")
            edge["source"] = payload.get("source") or edge.get("source")
            return edge
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return {}


def _avg_tf_score(timeframes: dict, names: tuple[str, ...], default: float = 50.0) -> float:
    vals = []
    for name in names:
        row = timeframes.get(name) if isinstance(timeframes, dict) else {}
        try:
            vals.append(float((row or {}).get("score")))
        except (TypeError, ValueError):
            continue
    return sum(vals) / len(vals) if vals else default


def _apply_historical_mtf_overlay(entry: dict) -> dict:
    """Promote true historical MTF into horizon/score decisions.

    `_scanner_intel` can run even when historical candles are missing.  When the
    MTF cache exists, this overlay makes the row much more useful for the user:
    fast TFs decide intraday chaseability; 1H/4H/1D decide swing/carry quality.
    """
    mtf = entry.get("mtf") if isinstance(entry.get("mtf"), dict) else {}
    timeframes = entry.get("mtf_timeframes") if isinstance(entry.get("mtf_timeframes"), dict) else {}
    if not mtf and not timeframes:
        return entry
    source = str(entry.get("mtf_source") or "").lower()
    if source in {"missing", ""}:
        return entry

    decision = str(entry.get("trade_decision") or "HOLD").upper()
    bias = str(entry.get("mtf_trade_bias") or "").upper()
    if bias in {"BUY CE", "BUY PE"}:
        entry["historical_mtf_trade_bias"] = bias

    fast_score = _avg_tf_score(timeframes, ("1M", "5M", "15M"), float(entry.get("mtf_score") or 50))
    higher_score = _avg_tf_score(timeframes, ("1H", "4H", "1D"), float(entry.get("mtf_score") or 50))
    fast_bull = sum(1 for tf in ("1M", "5M", "15M") if mtf.get(tf) == "BULL")
    fast_bear = sum(1 for tf in ("1M", "5M", "15M") if mtf.get(tf) == "BEAR")
    higher_bull = sum(1 for tf in ("1H", "4H", "1D") if mtf.get(tf) == "BULL")
    higher_bear = sum(1 for tf in ("1H", "4H", "1D") if mtf.get(tf) == "BEAR")
    is_ce = decision == "BUY CE"
    is_pe = decision == "BUY PE"
    fast_aligned = (is_ce and fast_bull >= 2) or (is_pe and fast_bear >= 2)
    higher_aligned = (is_ce and higher_bull >= 2) or (is_pe and higher_bear >= 2)
    higher_opposes = (is_ce and higher_bear >= 2) or (is_pe and higher_bull >= 2)

    option_score = float(entry.get("option_readiness") or 0)
    setup_score = float(entry.get("setup_strength") or entry.get("readiness") or 0)
    rel_vol = float(entry.get("rel_vol") or 0)
    sector = float(entry.get("sector_strength") or 50)
    intraday = min(100.0, max(0.0, option_score * 0.30 + setup_score * 0.20 + fast_score * 0.32 + min(rel_vol / 3.0, 1.0) * 18))
    swing = min(100.0, max(0.0, option_score * 0.18 + setup_score * 0.18 + higher_score * 0.34 + sector * 0.15 + (10 if higher_aligned else 0) + (6 if fast_aligned else 0)))
    if higher_opposes:
        swing = min(swing, 58.0)
    entry["intraday_score"] = round(max(float(entry.get("intraday_score") or 0), intraday), 1)
    entry["swing_score"] = round(max(float(entry.get("swing_score") or 0), swing), 1)
    entry["historical_fast_score"] = round(fast_score, 1)
    entry["historical_higher_score"] = round(higher_score, 1)
    entry["historical_mtf_alignment"] = (
        "FAST_AND_HIGHER_ALIGNED" if fast_aligned and higher_aligned
        else "FAST_ONLY" if fast_aligned
        else "HIGHER_ONLY" if higher_aligned
        else "HIGHER_OPPOSES" if higher_opposes
        else "MIXED"
    )

    if is_ce or is_pe:
        if higher_aligned and entry["swing_score"] >= 72:
            entry["trade_horizon"] = "SWING"
            entry["sustain_rule"] = "15M close + 1H hold"
            entry["horizon_reason"] = "Historical 1H/4H/1D support this move; eligible for swing/BTST watch if option contract remains clean."
        elif fast_aligned and entry["intraday_score"] >= 64:
            entry["trade_horizon"] = "INTRADAY"
            entry["sustain_rule"] = "5M or 15M close"
            entry["horizon_reason"] = "Historical fast timeframes support intraday only; book/exit if 15M fails."
        elif higher_opposes:
            entry["trade_horizon"] = "INTRADAY"
            entry["sustain_rule"] = "5M close only; no carry"
            entry["horizon_reason"] = "Fast setup exists but higher timeframe opposes; no swing carry."

    side_text = (
        "bullish"
        if is_ce or bias == "BUY CE"
        else "bearish"
        if is_pe or bias == "BUY PE"
        else "neutral"
    )
    entry["mtf_decision_note"] = (
        f"Historical MTF: {entry.get('historical_mtf_alignment')} · "
        f"fast {fast_score:.0f}, higher {higher_score:.0f}, {side_text} plan."
    )
    primary_side = str(entry.get("trade_decision") or "HOLD").upper()
    mtf_side = str(entry.get("historical_mtf_trade_bias") or "").upper()
    if mtf_side in {"BUY CE", "BUY PE"} and mtf_side != primary_side:
        alt_entry = (
            float(entry.get("positive_above") or entry.get("entry_price_hint") or 0)
            if mtf_side == "BUY CE"
            else float(entry.get("negative_below") or entry.get("entry_price_hint") or 0)
        )
        alt_invalidation = (
            float(entry.get("negative_below") or entry.get("stop_loss_hint") or 0)
            if mtf_side == "BUY CE"
            else float(entry.get("positive_above") or entry.get("stop_loss_hint") or 0)
        )
        sustain = (
            "15M close + 1H hold"
            if entry.get("historical_mtf_alignment") in {"HIGHER_ONLY", "FAST_AND_HIGHER_ALIGNED"}
            else "5M/15M close"
        )
        entry["mtf_conflict"] = True
        entry["mtf_conflict_note"] = (
            f"Scanner primary is {primary_side}; historical MTF suggests {mtf_side}. "
            "Keep these as separate maps and wait for the correct side trigger."
        )
        entry["mtf_alternate_trade_map"] = _side_trade_map(
            side=mtf_side,
            entry=alt_entry,
            invalidation=alt_invalidation,
            sustain_rule=sustain,
            source="historical_mtf_alternate",
            reason=entry["mtf_decision_note"],
            atr=float(entry.get("underlying_risk_points") or 0),
        )
    else:
        entry["mtf_conflict"] = False
        entry["mtf_conflict_note"] = ""
        entry["mtf_alternate_trade_map"] = None
    return entry


def _decode_option_cache(raw) -> dict:
    if not raw:
        return {}
    try:
        value = raw.decode() if isinstance(raw, bytes) else raw
        payload = json.loads(value)
        if isinstance(payload, dict) and payload.get("ready"):
            payload["option_chain_ready"] = True
            return payload
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return {}


def _days_to_event(event_date: str) -> int | None:
    try:
        return (date.fromisoformat(str(event_date)[:10]) - date.today()).days
    except Exception:
        return None


def _event_risk_from_raw(symbol: str, raw) -> dict:
    symbol = str(symbol or "").upper().strip()
    if not raw:
        return {
            "symbol": symbol,
            "entry_allowed": True,
            "event_type": "NONE",
            "next_event_date": "",
            "days_to_event": None,
            "block_reason": "",
            "source": "calendar_empty",
        }
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        payload = json.loads(text)
    except Exception:
        payload = {}
    next_date = str(payload.get("next_event_date") or payload.get("event_date") or "")
    days = _days_to_event(next_date)
    event_type = str(payload.get("event_type") or "EVENT").upper()
    blocked = days is not None and -1 <= days <= 2
    return {
        "symbol": symbol,
        "entry_allowed": not blocked,
        "event_type": event_type,
        "next_event_date": next_date,
        "days_to_event": days,
        "block_reason": f"hard block from T-2 to T+1 around {event_type.lower()}" if blocked else "",
        "source": str(payload.get("source") or "manual_calendar"),
        "note": str(payload.get("note") or ""),
    }


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value else []
    return []


@routes.get("/api/ticks/{symbol}")
async def get_tick(request):
    """Get latest tick data for a symbol."""
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]

    data = await redis.hgetall(f"infusion:tick:{symbol}")
    if not data:
        return web.json_response(
            {"error": f"No tick data for {symbol}"}, status=404
        )

    result = {"symbol": symbol}
    for k, v in data.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        try:
            result[key] = float(val)
        except (ValueError, TypeError):
            result[key] = val

    return web.json_response(result)


@routes.get("/api/ticks")
async def list_ticks(request):
    """Bulk tick data for all tracked symbols.

    Uses Redis pipeline to fetch all tick hashes in one round-trip.
    Returns array of tick objects for dashboard virtual scroll.

    Query params:
      ?sector={sector_id}  — filter by sector
      ?tier={nifty50|nifty100|nifty200|nifty500} — filter by index membership
    """
    redis = request.app["redis"]
    sector_filter = request.query.get("sector", "").upper()
    tier_filter = request.query.get("tier", "").upper()

    # Get symbol list from infusion:symbols hash
    all_symbols = await redis.hgetall("infusion:symbols")
    if not all_symbols:
        return web.json_response({"count": 0, "ticks": []})

    # Parse symbol metadata and apply filters
    symbols_to_fetch = []
    symbol_meta = {}

    for inst_key, meta_raw in all_symbols.items():
        try:
            if isinstance(meta_raw, bytes):
                meta = msgpack.unpackb(meta_raw, raw=False)
            else:
                meta = meta_raw
            symbol = meta.get("symbol", "")
            if not symbol:
                continue

            # Apply sector filter
            if sector_filter and meta.get("sector_id", "") != sector_filter:
                continue

            # Apply tier filter
            if tier_filter:
                membership = meta.get("index_membership", [])
                if tier_filter not in membership:
                    continue

            symbols_to_fetch.append(symbol)
            symbol_meta[symbol] = {
                "sector_id": meta.get("sector_id", ""),
                "market_cap_tier": meta.get("market_cap_tier", ""),
            }
        except Exception:
            continue

    if not symbols_to_fetch:
        return web.json_response({"count": 0, "ticks": []})

    index_context = await _scanner_index_context(redis)
    fo_ban_context = await _fo_ban_context(redis)
    vwap_state_context = await _vwap_state_context(redis, symbols_to_fetch)
    vwap_state_updates: dict[str, str] = {}

    # Pipeline: fetch tick, feature, pre-breakout, sector, MTF, cached news edge,
    # event calendar, and selected-symbol Upstox option-chain confirmation in
    # one round-trip.
    pipe = redis.pipeline()
    for symbol in symbols_to_fetch:
        pipe.hgetall(f"infusion:tick:{symbol}")
        pipe.hgetall(f"infusion:feature:{symbol}")
        pipe.hgetall(f"infusion:prebreak:{symbol}")
        pipe.hgetall(f"infusion:sector:{symbol_meta.get(symbol, {}).get('sector_id', '')}")
        pipe.get(f"infusion:mtf:{symbol}")
        pipe.get(f"{NEWS_EDGE_KEY_PREFIX}{symbol}")
        pipe.get(f"{EVENT_KEY_PREFIX}{symbol}")
        pipe.get(f"infusion:option-chain:{symbol}")
    results = await pipe.execute()

    # Build response
    ticks = []
    for i, symbol in enumerate(symbols_to_fetch):
        base = i * 8
        data = results[base]
        feature_data = results[base + 1]
        prebreak_data = results[base + 2]
        sector_data = results[base + 3]
        mtf_raw = results[base + 4]
        news_raw = results[base + 5]
        event_raw = results[base + 6]
        option_raw = results[base + 7]
        if not data:
            continue
        entry = {
            "symbol": symbol,
            "sector_id": symbol_meta.get(symbol, {}).get("sector_id", ""),
        }
        entry.update(_decode_hash(data))
        features = _decode_hash(feature_data) if feature_data else {}
        prebreak = _decode_hash(prebreak_data) if prebreak_data else {}
        sector = _decode_hash(sector_data) if sector_data else {}
        news_edge = _decode_news_cache(news_raw)
        event_risk = _event_risk_from_raw(symbol, event_raw)
        option_summary = _decode_option_cache(option_raw)
        sector_strength = float(sector.get("strength_score", 50) or 50)
        try:
            _apply_index_context(entry, index_context)
            _apply_fo_ban_context(entry, symbol, fo_ban_context)
            entry.update(_scanner_intel(entry, features, prebreak, sector_strength))
            # Phase R2 -- breakout_type classification. Needs the prior
            # poll's VWAP state (fetched once, in bulk, above) to tell a
            # reclaim apart from a continuation; the new state is queued
            # here and written back in one batched pipeline after the
            # loop, not per-row.
            entry["breakout_type"] = _classify_breakout_type(entry, vwap_state_context.get(symbol))
            if entry.get("vwap_state") in {"ABOVE", "BELOW"}:
                vwap_state_updates[symbol] = entry["vwap_state"]
            # Phase 13.5/13.7: VWAP SD bands, Heiken-Ashi, delivery % --
            # informational-only fields feature-engine already computes,
            # passed straight through (no derivation needed here, unlike
            # _scanner_intel's fields above). See feature_engine/main.py's
            # HOT_STATE_ML_WHITELIST for what actually lands in `features`.
            _BOOL_PASSTHROUGH_KEYS = {"vwap_sd_ready", "ha_doji", "ha_color_flip"}
            for key in (
                "delivery_pct", "vwap_stdev", "vwap_sd1_upper", "vwap_sd1_lower",
                "vwap_sd2_upper", "vwap_sd2_lower", "vwap_sd_ready",
                "ha_trend", "ha_trend_streak", "ha_doji", "ha_color_flip",
                "delivery_pct_avg_20d", "delivery_avg_days", "delivery_trade_date",
            ):
                if key not in features:
                    continue
                value = features[key]
                # delivery_pct_avg_20d is deliberately stored as "" (not
                # omitted) when there's no rolling average yet -- see
                # nse_scraper/delivery.py's _rolling_avg(). _decode_hash's
                # float() coercion leaves "" as the literal empty string
                # (float("") raises), which is truthy-by-existence in JS
                # (`!= null` passes) and broke the dashboard's .toFixed()
                # call on it live. Normalize to a real absence.
                if value == "":
                    continue
                # These round-tripped through Redis as str(bool) ("True"/
                # "False"), not real booleans -- _decode_hash's float()
                # coercion leaves them as that literal string since
                # float("True") raises. Coerce back explicitly rather than
                # letting a truthy-string "False" silently evaluate truthy
                # on the frontend.
                if key in _BOOL_PASSTHROUGH_KEYS and isinstance(value, str):
                    value = value == "True"
                entry[key] = value
            if option_summary:
                chain_score = float(option_summary.get("execution_score") or option_summary.get("option_score") or 0)
                previous_proxy = entry.get("option_readiness")
                entry.update({
                    "underlying_option_proxy": previous_proxy,
                    "option_readiness": chain_score,
                    "chain_option_score": chain_score,
                    "chain_raw_option_score": option_summary.get("raw_option_score"),
                    "chain_execution_status": option_summary.get("execution_status"),
                    "chain_trade_ready": bool(option_summary.get("trade_ready")),
                    "chain_quality_grade": option_summary.get("quality_grade"),
                    "chain_suggested_contract": option_summary.get("contract"),
                    "chain_score_cap_reason": option_summary.get("score_cap_reason") or "",
                    "chain_score_cap_detail": option_summary.get("score_cap_detail") or "",
                    "option_chain_ready": True,
                    "option_chain_source": "upstox_chain_cached",
                })
                # Phase R1 tier upgrade -- OPTION_READY needs real chain
                # data, which isn't available inside _scanner_intel's pure
                # computation. A stock already ranked BREAKOUT_NOW/
                # RETEST_ENTRY on its own evidence graduates to
                # OPTION_READY once the contract itself is confirmed
                # tradeable; never downgrades or invents readiness the
                # stock score didn't already earn.
                if entry.get("stock_breakout_tier") in {"BREAKOUT_NOW", "RETEST_ENTRY"} and entry.get("chain_trade_ready"):
                    entry["stock_breakout_tier"] = "OPTION_READY"
                metrics = option_summary.get("metrics") if isinstance(option_summary.get("metrics"), dict) else {}
                if metrics:
                    entry.update({
                        "chain_spread_pct": metrics.get("spread_pct"),
                        "chain_oi": metrics.get("oi"),
                        "chain_oi_change_pct": metrics.get("oi_change_pct"),
                        "chain_iv": metrics.get("iv"),
                        "chain_iv_rank": metrics.get("iv_rank"),
                        "chain_delta": metrics.get("delta"),
                        "chain_expiry_days": metrics.get("expiry_days"),
                    })
                    oi_buildup = _classify_oi_buildup(metrics.get("oi_change_pct"), features.get("change_pct"))
                    entry["chain_oi_signal"] = oi_buildup["label"]
                    entry["chain_oi_bias"] = oi_buildup["bias"]
            entry.update(build_intelligence_layer(entry, features, option_summary=option_summary, news_edge=news_edge, event_risk=event_risk))
            mtf_cache = _decode_mtf_cache(mtf_raw)
            if mtf_cache:
                existing_rejections = _as_list(entry.get("rejection_reasons"))
                mtf_rejections = _as_list(mtf_cache.get("mtf_rejection_reasons"))
                entry.update(mtf_cache)
                if mtf_cache.get("mtf_trade_bias") in {"BUY CE", "BUY PE"}:
                    entry["historical_mtf_trade_bias"] = mtf_cache["mtf_trade_bias"]
                combined_rejections = list(dict.fromkeys(existing_rejections + mtf_rejections))
                entry["rejection_reasons"] = combined_rejections[:6]
                entry = _apply_historical_mtf_overlay(entry)
                entry.update(build_intelligence_layer(entry, features, option_summary=option_summary, news_edge=news_edge, event_risk=event_risk))
        except Exception as exc:
            logger.warning(
                "tick_scanner_enrichment_failed",
                symbol=symbol,
                error=str(exc),
            )
            continue
        ticks.append(entry)

    # Phase R1 -- rvol_rank needs the full set to rank against, so it's a
    # cheap second pass over the already-built list rather than something
    # any single row can compute for itself. 1 = highest relative volume
    # in this response. Ties keep stable insertion order (Python sort is
    # stable), not a real concern at this granularity.
    by_rvol = sorted(ticks, key=lambda e: float(e.get("rel_vol") or 0), reverse=True)
    for rank, entry in enumerate(by_rvol, start=1):
        entry["rvol_rank"] = rank

    await _write_vwap_state_context(redis, vwap_state_updates)

    return web.json_response({"count": len(ticks), "ticks": ticks})


@routes.get("/api/ticks/snapshot")
async def snapshot(request):
    """Full dashboard snapshot — ticks + features in one request.

    Used for initial dashboard load. Returns tick and feature data
    for all symbols so the dashboard can render immediately without
    waiting for WebSocket updates.
    """
    redis = request.app["redis"]

    # Get all symbols
    all_symbols = await redis.hgetall("infusion:symbols")
    if not all_symbols:
        return web.json_response({"count": 0, "snapshot": []})

    symbols = []
    for inst_key, meta_raw in all_symbols.items():
        try:
            meta = msgpack.unpackb(meta_raw, raw=False) if isinstance(meta_raw, bytes) else meta_raw
            sym = meta.get("symbol", "")
            if sym and meta.get("segment") != "INDEX":
                symbols.append((sym, meta))
        except Exception:
            continue

    # Pipeline: tick + feature for each symbol
    pipe = redis.pipeline()
    for sym, _ in symbols:
        pipe.hgetall(f"infusion:tick:{sym}")
        pipe.hgetall(f"infusion:feature:{sym}")
    results = await pipe.execute()

    snapshot = []
    for i, (sym, meta) in enumerate(symbols):
        tick_data = results[i * 2]
        feature_data = results[i * 2 + 1]

        entry = {
            "symbol": sym,
            "sector_id": meta.get("sector_id", ""),
            "market_cap_tier": meta.get("market_cap_tier", ""),
        }

        # Merge tick data
        if tick_data:
            for k, v in tick_data.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                try:
                    entry[key] = float(val)
                except (ValueError, TypeError):
                    entry[key] = val

        # Merge feature data
        if feature_data:
            for k, v in feature_data.items():
                key = k.decode() if isinstance(k, bytes) else k
                if key not in entry:  # don't overwrite tick fields
                    val = v.decode() if isinstance(v, bytes) else v
                    try:
                        entry[f"f_{key}"] = float(val)
                    except (ValueError, TypeError):
                        entry[f"f_{key}"] = val

        snapshot.append(entry)

    return web.json_response({"count": len(snapshot), "snapshot": snapshot})


@routes.get("/api/symbols")
async def list_symbols(request):
    """List all loaded symbols with metadata.

    Returns the symbol universe currently in Redis.
    Query params:
      ?sector={sector_id}  — filter by sector
      ?tier={nifty50|nifty100|nifty200|nifty500} — filter by tier
    """
    redis = request.app["redis"]
    sector_filter = request.query.get("sector", "").upper()
    tier_filter = request.query.get("tier", "").upper()

    all_symbols = await redis.hgetall("infusion:symbols")
    symbols = []

    for inst_key, meta_raw in all_symbols.items():
        try:
            meta = msgpack.unpackb(meta_raw, raw=False) if isinstance(meta_raw, bytes) else meta_raw

            if sector_filter and meta.get("sector_id", "") != sector_filter:
                continue
            if tier_filter and tier_filter not in meta.get("index_membership", []):
                continue

            symbols.append({
                "symbol": meta.get("symbol", ""),
                "sector_id": meta.get("sector_id", ""),
                "exchange": meta.get("exchange", "NSE"),
                "market_cap_tier": meta.get("market_cap_tier", ""),
                "index_membership": meta.get("index_membership", []),
            })
        except Exception:
            continue

    symbols.sort(key=lambda x: x["symbol"])
    return web.json_response({"count": len(symbols), "symbols": symbols})
