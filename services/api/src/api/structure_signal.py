"""Structure & Breakout Suite -- Phase 1/2 (2026-08-29): the signal
engine half of the "NSE Pro Smart Structure & Breakout Suite" PineScript
port. Computes a literal x/7 bull/bear setup score, a breakout trigger
price, candle confirmation, and R-multiple risk levels for one symbol/
timeframe on request -- the live-dashboard half of the system. The
historical replay backtester and auto-optimizer (Sections H/I of the
approved architecture) are Phase 3, deliberately not built here.

This is a decision-support engine, not a promise. Every response carries
DISCLAIMER below verbatim: this identifies a statistically stronger
setup CANDIDATE, never a perfect or guaranteed trade -- the same
"never fabricate, never overclaim" posture this codebase already applies
everywhere else (api.screener_hydrator, api.smc_geometry, etc.).

Maximal reuse, per the approved architecture's own Section 0 finding --
nothing below reimplements an indicator or structure-detection pass this
service already has:
  - EMA200/RSI14/MACD/Supertrend/VWAP/ATR: api.routes.mtf's own
    `_indicators()` (IndicatorPack) -- the exact same real, tested
    per-timeframe toolkit mtf.py's own MTF confidence engine runs on.
  - Historical bar I/O: api.routes.mtf's own `_load_bars()` (the same
    infusion:ohlc:{symbol}:* Redis zsets every other chart/MTF/backtest
    route already reads) and api.routes.charts's own `_aggregate()`
    (the identical real bucketing the candlestick series itself renders
    on, not a second approximation).
  - Pivot highs/lows, trend state, and trendline geometry: api.
    smc_geometry's own `compute_smc_geometry()` -- a pure batch replay
    of feature-engine's real structure rules, already built for the
    chart overlay's own GET /api/chart/smc route.
  - Volatility (Bollinger-inside-Keltner) and RVOL: api.screener_
    hydrator's own `compute_squeeze_readiness()`/`compute_rvol()`,
    already real, already tested this sprint.
  - Symbol universe: api.routes.screener's own `_symbol_universe()`.

Genuinely new here (confirmed absent elsewhere in this codebase before
writing this module): Money Flow Index (`_mfi`) and the literal x/7
bias/trigger/candle-confirmation/risk-engine composition itself -- the
PineScript's own specific rules, which don't exist anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

from api.routes.charts import _aggregate
from api.routes.mtf import IndicatorPack, _indicators, _load_bars
from api.routes.screener import _symbol_universe
from api.screener_hydrator import compute_rvol, compute_squeeze_readiness

Payload = dict[str, Any]
Bar = dict[str, Any]

DISCLAIMER = (
    "Decision-support engine output -- a statistically stronger setup "
    "candidate based on the rules and history available right now, "
    "never a perfect or guaranteed trade."
)

# Literal PineScript defaults, per the approved spec -- every one of
# these is deliberately a plain module constant (not buried in a class
# body) so Phase 3's optimizer can sweep them by name without reaching
# into an object's internals.
DEFAULT_MIN_SETUP_QUALITY = 5
DEFAULT_MIN_BIAS_EDGE = 1
DEFAULT_FAST_TRIGGER_LOOKBACK = 12
DEFAULT_ATR_BREAKOUT_BUFFER = 0.20
DEFAULT_STRICT_STOP_MAX_ATR = 1.15
DEFAULT_TP1_R = 1.5
DEFAULT_TP2_R = 2.5
DEFAULT_TP3_R = 3.5
DEFAULT_MIN_BODY_PCT = 0.45
DEFAULT_BULLISH_CLOSE_LOCATION_MIN = 0.75
DEFAULT_BEARISH_CLOSE_LOCATION_MAX = 0.25
# Not named in the approved spec's own default list -- disclosed,
# reasonable starting points a Phase 3 optimizer sweep should tune
# against real outcomes rather than trust blindly.
DEFAULT_RSI_BULLISH_MIN = 55.0
DEFAULT_RSI_BEARISH_MAX = 45.0
DEFAULT_RVOL_CONFIRM_MIN = 1.2
DEFAULT_SQUEEZE_ENERGY_MIN = 50.0  # matches Screener.tsx's own SQUEEZE_COILING_THRESHOLD
DEFAULT_MOMENTUM_WATCH_ATR = 0.5  # within this many ATRs of the trigger counts as "watching"

# 3m is not one of api.routes.mtf's own named TIMEFRAMES, but its
# underlying _aggregate() buckets by an arbitrary minute width already
# (charts.py's own /api/chart/smc route only ever wires up 1/5/15/60/240
# as NAMED intervals) -- this is the same real aggregation, just a third
# named width, not a new capability.
TIMEFRAME_MINUTES: dict[str, int | None] = {
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": None,  # None = use daily bars directly, no aggregation
}

_IST = ZoneInfo("Asia/Kolkata")
_SESSION_OPEN = dt_time(9, 15)


@dataclass(frozen=True)
class StructureSignalConfig:
    """Every tunable in the approved spec, as one plain dataclass so a
    single object can be threaded through the whole engine and Phase 3's
    optimizer can construct variants of it directly from a parameter
    grid entry."""

    min_setup_quality: int = DEFAULT_MIN_SETUP_QUALITY
    min_bias_edge: int = DEFAULT_MIN_BIAS_EDGE
    fast_trigger_lookback: int = DEFAULT_FAST_TRIGGER_LOOKBACK
    atr_breakout_buffer: float = DEFAULT_ATR_BREAKOUT_BUFFER
    strict_stop_max_atr: float = DEFAULT_STRICT_STOP_MAX_ATR
    tp1_r: float = DEFAULT_TP1_R
    tp2_r: float = DEFAULT_TP2_R
    tp3_r: float = DEFAULT_TP3_R
    min_body_pct: float = DEFAULT_MIN_BODY_PCT
    bullish_close_location_min: float = DEFAULT_BULLISH_CLOSE_LOCATION_MIN
    bearish_close_location_max: float = DEFAULT_BEARISH_CLOSE_LOCATION_MAX
    rsi_bullish_min: float = DEFAULT_RSI_BULLISH_MIN
    rsi_bearish_max: float = DEFAULT_RSI_BEARISH_MAX
    rvol_confirm_min: float = DEFAULT_RVOL_CONFIRM_MIN
    squeeze_energy_min: float = DEFAULT_SQUEEZE_ENERGY_MIN
    momentum_watch_atr: float = DEFAULT_MOMENTUM_WATCH_ATR
    trade_mode: str = "BALANCED"  # "BALANCED" | "STRICT"
    vwap_enabled: bool = True


DEFAULT_CONFIG = StructureSignalConfig()


@dataclass(frozen=True)
class Trigger:
    side: str  # "BUY_ABOVE" | "SELL_BELOW"
    price: float
    source: str  # "fast_range" | "swing_zone" | "trendline"


@dataclass(frozen=True)
class RiskLevels:
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    risk_per_share: float


# ─────────────────────────── pure indicators ───────────────────────────


def _mfi(bars: list[Bar], period: int = 14) -> float | None:
    """Money Flow Index -- genuinely absent everywhere else in this
    codebase (confirmed by search before writing this function, per the
    approved architecture's own disclosed gap). Standard formula: typical
    price = (H+L+C)/3; a rising typical price assigns that bar's raw
    money flow (typical price * volume) to the positive side, a falling
    one to the negative side; MFI = 100 - 100/(1 + positive/negative).
    None (never a fabricated 0/50) when fewer than `period + 1` bars, or
    every single bar in the window is flat (no positive AND no negative
    flow to form a ratio from)."""
    if len(bars) < period + 1:
        return None
    typical = [(float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0 for b in bars]
    raw_flow = [typical[i] * float(bars[i]["volume"]) for i in range(len(bars))]
    window = range(len(bars) - period, len(bars))
    positive = sum(raw_flow[i] for i in window if typical[i] > typical[i - 1])
    negative = sum(raw_flow[i] for i in window if typical[i] < typical[i - 1])
    if positive == 0 and negative == 0:
        return None
    if negative == 0:
        return 100.0
    money_ratio = positive / negative
    return round(100.0 - 100.0 / (1.0 + money_ratio), 1)


def _is_intraday_timeframe(timeframe: str) -> bool:
    return TIMEFRAME_MINUTES.get(timeframe) is not None


def _htf_for(timeframe: str) -> str:
    """Section G's own default HTF filter rule: scalp/intraday
    timeframes confirm against 1H, 1H/4H confirm against Daily. A daily
    request has no genuinely higher timeframe available in this system
    -- it confirms against itself, disclosed via htf_is_self in the
    response rather than silently faked as a real higher-timeframe read."""
    if timeframe in ("3m", "5m", "15m"):
        return "1h"
    if timeframe in ("1h", "4h"):
        return "1d"
    return "1d"


def _vwap_session(intraday_1m: list[Bar]) -> float | None:
    """Real session-anchored VWAP: cumulative typical-price*volume over
    cumulative volume, from the CURRENT IST session's own 9:15 open only
    -- not the trailing N bars api.routes.mtf's own `_vwap()` uses for
    its rolling proxy (that one is deliberately a rolling window, not
    session-anchored; see this module's own docstring for why a genuine
    session anchor is worth a small, separate real function rather than
    reusing that one for a different question). None when there are no
    bars yet from today's session (e.g. before the open, or a symbol
    with no live 1-minute history today)."""
    if not intraday_1m:
        return None
    last_ts = int(intraday_1m[-1]["time"])
    today = datetime.fromtimestamp(last_ts, tz=_IST).date()
    session_start = datetime.combine(today, _SESSION_OPEN, tzinfo=_IST).timestamp()
    session_bars = [b for b in intraday_1m if float(b["time"]) >= session_start]
    if not session_bars:
        return None
    cum_pv = 0.0
    cum_vol = 0.0
    for b in session_bars:
        typical = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
        vol = float(b["volume"])
        cum_pv += typical * vol
        cum_vol += vol
    if cum_vol <= 0:
        return None
    return round(cum_pv / cum_vol, 2)


# ─────────────────────────── A. Bias Engine ───────────────────────────


@dataclass(frozen=True)
class SubscoreReads:
    """Every real reading the bias engine's 7 subscores are built from,
    assembled once by compute_structure_signal() and passed in here --
    keeps the actual scoring logic a pure, hand-testable function with
    no Redis/IndicatorPack coupling of its own."""

    close: float
    ema200: float | None
    htf_trend_state: int  # from compute_smc_geometry: 1 bullish, -1 bearish, 0 range
    supertrend: str  # "BULL" | "BEAR" | "MIXED"
    rsi14: float | None
    mfi: float | None
    squeeze_readiness: float | None
    bb_width_expanding: bool
    rvol: float | None
    vwap: float | None


def bullish_subscores(r: SubscoreReads, config: StructureSignalConfig) -> list[bool]:
    momentum = (r.rsi14 is not None and r.rsi14 >= config.rsi_bullish_min) or (
        r.mfi is not None and r.mfi >= config.rsi_bullish_min
    )
    volatility_active = r.bb_width_expanding or (
        r.squeeze_readiness is not None and r.squeeze_readiness >= config.squeeze_energy_min
    )
    return [
        r.ema200 is not None and r.close > r.ema200,
        r.htf_trend_state == 1,
        r.supertrend == "BULL",
        momentum,
        volatility_active,
        r.rvol is not None and r.rvol >= config.rvol_confirm_min,
        (not config.vwap_enabled) or r.vwap is None or r.close > r.vwap,
    ]


def bearish_subscores(r: SubscoreReads, config: StructureSignalConfig) -> list[bool]:
    momentum = (r.rsi14 is not None and r.rsi14 <= config.rsi_bearish_max) or (
        r.mfi is not None and r.mfi <= config.rsi_bearish_max
    )
    volatility_active = r.bb_width_expanding or (
        r.squeeze_readiness is not None and r.squeeze_readiness >= config.squeeze_energy_min
    )
    return [
        r.ema200 is not None and r.close < r.ema200,
        r.htf_trend_state == -1,
        r.supertrend == "BEAR",
        momentum,
        volatility_active,
        r.rvol is not None and r.rvol >= config.rvol_confirm_min,
        (not config.vwap_enabled) or r.vwap is None or r.close < r.vwap,
    ]


def compute_setup_scores(r: SubscoreReads, config: StructureSignalConfig) -> tuple[int, int]:
    """The literal x/7 scores. STRICT mode collapses each side to 7 (all
    seven of that side's own conditions agree) or 0 -- "require all
    selected filters to agree," per the approved spec's own Trade Mode
    rule -- BALANCED mode is the plain subscore count."""
    bull = bullish_subscores(r, config)
    bear = bearish_subscores(r, config)
    if config.trade_mode == "STRICT":
        return (7 if all(bull) else 0), (7 if all(bear) else 0)
    return sum(bull), sum(bear)


def compute_dominant_bias(bull_score: int, bear_score: int, config: StructureSignalConfig) -> str:
    """Directional lean ONLY -- the edge-vs-close-scores rule the
    approved spec states in plain English ("if both sides are close or
    equal, show No Clear Bias"). Does NOT itself apply the setup-quality
    floor; see compute_trade_readiness() for why that's a deliberately
    separate gate (LOW_QUALITY needs to be distinguishable from a
    genuinely ambiguous tie, which collapsing both into one field can't
    express)."""
    edge = bull_score - bear_score
    if edge >= config.min_bias_edge:
        return "BULLISH"
    if -edge >= config.min_bias_edge:
        return "BEARISH"
    return "NO_CLEAR_BIAS"


def compute_trade_readiness(
    *,
    dominant_bias: str,
    bull_score: int,
    bear_score: int,
    config: StructureSignalConfig,
    trigger_present: bool,
    candle_confirmed: bool,
) -> str:
    """The actionability gate layered on top of dominant_bias: a real
    directional lean can still be NOT ready to trade for two distinct,
    separately-surfaced reasons -- LOW_QUALITY (the leaning side hasn't
    cleared min_setup_quality yet) vs WAIT (quality is fine, just no
    trigger/confirmed candle yet)."""
    if dominant_bias == "NO_CLEAR_BIAS":
        return "NO_CLEAR_BIAS"
    score = bull_score if dominant_bias == "BULLISH" else bear_score
    if score < config.min_setup_quality:
        return "LOW_QUALITY"
    if not trigger_present or not candle_confirmed:
        return "WAIT"
    return "BUY_ARMED" if dominant_bias == "BULLISH" else "SELL_ARMED"


# ─────────────────────────── B. Breakout Trigger Engine ───────────────────────────


def _fast_range_trigger(
    bars: list[Bar], atr: float | None, bullish: bool, config: StructureSignalConfig
) -> Trigger | None:
    if atr is None or atr <= 0 or len(bars) < config.fast_trigger_lookback:
        return None
    window = bars[-config.fast_trigger_lookback :]
    buffer = atr * config.atr_breakout_buffer
    if bullish:
        recent_high = max(float(b["high"]) for b in window)
        return Trigger("BUY_ABOVE", round(recent_high + buffer, 2), "fast_range")
    recent_low = min(float(b["low"]) for b in window)
    return Trigger("SELL_BELOW", round(recent_low - buffer, 2), "fast_range")


def _swing_zone_trigger(
    geometry: Payload, atr: float | None, bullish: bool, config: StructureSignalConfig
) -> Trigger | None:
    if atr is None or atr <= 0:
        return None
    buffer = atr * config.atr_breakout_buffer
    if bullish:
        resistance = geometry.get("swing_high_1")
        if resistance is None:
            return None
        return Trigger("BUY_ABOVE", round(float(resistance) + buffer, 2), "swing_zone")
    support = geometry.get("swing_low_1")
    if support is None:
        return None
    return Trigger("SELL_BELOW", round(float(support) - buffer, 2), "swing_zone")


def _trendline_trigger(geometry: Payload, ltp: float, bullish: bool) -> Trigger | None:
    """Section B.3: a lower-high resistance trendline (this module's own
    `compute_smc_geometry` labels this the "bearish"-direction line --
    it only exists while trend_state is -1, i.e. a downtrend) becomes a
    BUY breakout trigger once price is still below it; the mirror
    "bullish"-direction (ascending support) line becomes a SELL
    breakdown trigger while price is still above it.

    Two independent reasons this never goes stale, both real: (1)
    compute_smc_geometry() only ever returns a trendline for whichever
    direction the STRUCTURE is currently in -- once a real close-based
    break flips trend_state, the next call simply stops returning this
    line at all; (2) this function's own explicit `ltp` check catches
    the faster case -- current price already trading beyond the line's
    projected value RIGHT NOW, even before a bar has closed to confirm
    that break -- so a live reader is never shown a "BUY ABOVE X" trigger
    the price has already, visibly, traded through."""
    for trendline in geometry.get("trendlines", []):
        points = trendline.get("points") or []
        if len(points) < 2:
            continue
        current_value = float(points[1]["value"])
        if bullish and trendline.get("direction") == "bearish" and ltp < current_value:
            return Trigger("BUY_ABOVE", current_value, "trendline")
        if not bullish and trendline.get("direction") == "bullish" and ltp > current_value:
            return Trigger("SELL_BELOW", current_value, "trendline")
    return None


def select_breakout_trigger(
    *,
    bars: list[Bar],
    geometry: Payload,
    atr: float | None,
    ltp: float,
    bullish: bool,
    config: StructureSignalConfig,
) -> Trigger | None:
    """Closest valid breakout level to LTP, in the bias direction --
    "use the closest valid breakout level in the direction of bias,"
    per the approved spec."""
    candidates = [
        t
        for t in (
            _fast_range_trigger(bars, atr, bullish, config),
            _swing_zone_trigger(geometry, atr, bullish, config),
            _trendline_trigger(geometry, ltp, bullish),
        )
        if t is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda t: abs(t.price - ltp))


# ─────────────────────────── C. Candle Structure Confirmation ───────────────────────────


def candle_confirms(
    bar: Bar, trigger_price: float, bullish: bool, config: StructureSignalConfig
) -> bool:
    high, low = float(bar["high"]), float(bar["low"])
    rng = high - low
    if rng <= 0:
        return False
    open_, close = float(bar["open"]), float(bar["close"])
    body_ratio = abs(close - open_) / rng
    if body_ratio < config.min_body_pct:
        return False
    close_location = (close - low) / rng
    if bullish:
        return (
            close > trigger_price
            and close_location >= config.bullish_close_location_min
            and close > open_
        )
    return (
        close < trigger_price
        and close_location <= config.bearish_close_location_max
        and close < open_
    )


def is_momentum_watch(ltp: float, trigger: Trigger | None, atr: float | None) -> bool:
    """Price approaching (not yet at) the trigger -- within
    DEFAULT_MOMENTUM_WATCH_ATR of it, on the near side, per the approved
    spec's own "Momentum Watch before breakout" requirement."""
    if trigger is None or atr is None or atr <= 0:
        return False
    band = atr * DEFAULT_MOMENTUM_WATCH_ATR
    if trigger.side == "BUY_ABOVE":
        return trigger.price - band <= ltp < trigger.price
    return trigger.price < ltp <= trigger.price + band


# ─────────────────────────── E. Risk Engine ───────────────────────────


def compute_risk_levels(
    *,
    entry: float,
    structure_level: float | None,
    atr: float,
    bullish: bool,
    config: StructureSignalConfig,
) -> RiskLevels | None:
    """For long: SL = max(structure support, entry - strict ATR stop);
    for short: SL = min(structure resistance, entry + strict ATR stop).
    Real structure can only ever TIGHTEN the stop, never widen it past
    the ATR cap -- see this module's own commit notes for the worked
    example. None (never a fabricated risk figure) when the resulting
    risk is zero or negative -- a structure level sitting on the wrong
    side of entry, or a degenerate zero ATR."""
    atr_stop_distance = config.strict_stop_max_atr * atr
    if bullish:
        atr_sl = entry - atr_stop_distance
        sl = max(structure_level, atr_sl) if structure_level is not None else atr_sl
        risk = entry - sl
        if risk <= 0:
            return None
        return RiskLevels(
            entry=entry,
            sl=round(sl, 2),
            tp1=round(entry + risk * config.tp1_r, 2),
            tp2=round(entry + risk * config.tp2_r, 2),
            tp3=round(entry + risk * config.tp3_r, 2),
            risk_per_share=round(risk, 2),
        )
    atr_sl = entry + atr_stop_distance
    sl = min(structure_level, atr_sl) if structure_level is not None else atr_sl
    risk = sl - entry
    if risk <= 0:
        return None
    return RiskLevels(
        entry=entry,
        sl=round(sl, 2),
        tp1=round(entry - risk * config.tp1_r, 2),
        tp2=round(entry - risk * config.tp2_r, 2),
        tp3=round(entry - risk * config.tp3_r, 2),
        risk_per_share=round(risk, 2),
    )


# ─────────────────────────── Travel status & visuals ───────────────────────────


def compute_travel_status(
    *,
    trade_readiness: str,
    open_position: Payload | None = None,
) -> str:
    """WAITING/ARMED are all this stateless, single-request signal
    endpoint can ever honestly report -- TRAIL_AFTER_TP1/TRAIL_TO_TP3/
    TP3_HIT/EXIT_PROTECT all describe an OPEN position's progress
    against targets it has already crossed, which requires a persisted
    position to check against. That tracking is Phase 3's job (the
    replay engine, and eventually a live paper-position tracker) --
    `open_position` is accepted here now so this function is already
    complete and independently testable for all six states, with the
    live route simply never having one to pass yet. Disclosed, not
    silently faked: this function's own logic, never the live route,
    is what a fabricated TRAIL_AFTER_TP1 would look like."""
    if open_position is None:
        if trade_readiness in ("BUY_ARMED", "SELL_ARMED"):
            return "ARMED"
        return "WAITING"
    if open_position.get("exit_reason") == "SL_HIT":
        return "EXIT_PROTECT"
    highest_hit = open_position.get("highest_target_hit")
    if highest_hit == "TP3":
        return "TP3_HIT"
    if highest_hit == "TP2":
        return "TRAIL_TO_TP3"
    if highest_hit == "TP1":
        return "TRAIL_AFTER_TP1"
    return "ARMED"


def compute_visual_markers(
    *,
    dominant_bias: str,
    momentum_watch: bool,
    breakout_confirmed: bool,
) -> Payload:
    """Section D's dot/diamond/triangle vocabulary. `dot` here is the
    single-timeframe bias read (green/red/gray); real per-timeframe MTF
    dots for the confirmation strip come from api.smc_geometry's own
    trend_state at each timeframe, assembled by the caller -- this
    function only derives the markers that depend on THIS signal's own
    bias/momentum/breakout state."""
    if dominant_bias == "BULLISH":
        dot = "GREEN"
    elif dominant_bias == "BEARISH":
        dot = "RED"
    else:
        dot = "GRAY"
    bullish = dominant_bias == "BULLISH"
    return {
        "dot": dot,
        "momentum_diamond": (
            ("GREEN" if bullish else "RED")
            if momentum_watch and dominant_bias != "NO_CLEAR_BIAS"
            else None
        ),
        "breakout_triangle": (
            ("GREEN" if bullish else "RED")
            if breakout_confirmed and dominant_bias != "NO_CLEAR_BIAS"
            else None
        ),
    }


# ─────────────────────────── D. Visual interpretation label ───────────────────────────


def build_interpretation_label(
    *,
    dominant_bias: str,
    trigger: Trigger | None,
    bull_score: int,
    bear_score: int,
    momentum_watch: bool,
    candle_confirmed: bool = False,
) -> list[str]:
    """The exact 4-line format from the approved spec's own worked
    examples -- generated from real computed state, never hand-formatted
    per call site, so the wording can't drift from what the engine
    actually decided.

    The spec's own three worked examples only cover "watching momentum"
    and "no clear bias" -- confirmed live against real market data (see
    this module's own commit notes) that a fourth, real state exists:
    the breakout candle has ALREADY confirmed and the trade is armed.
    `candle_confirmed` distinguishes that case so this line never keeps
    saying "awaiting confirmation" about a candle that already
    confirmed -- caught by testing this endpoint against live symbols,
    not a hypothetical."""
    quality = f"Quality B:{bull_score}/7 S:{bear_score}/7"
    if dominant_bias == "BULLISH" and trigger is not None:
        if candle_confirmed:
            status_line = "Breakout Confirmed"
        elif momentum_watch:
            status_line = "Watch BUY Momentum"
        else:
            status_line = "Awaiting confirmation candle"
        return ["BUY SIDE ONLY", f"Go above {trigger.price:.2f}", quality, status_line]
    if dominant_bias == "BEARISH" and trigger is not None:
        if candle_confirmed:
            status_line = "Breakout Confirmed"
        elif momentum_watch:
            status_line = "Watch SELL Momentum"
        else:
            status_line = "Awaiting confirmation candle"
        return ["SELL SIDE ONLY", f"Go below {trigger.price:.2f}", quality, status_line]
    return [
        "WAIT",
        "No clean breakout level",
        quality,
        "No Clear Bias" if dominant_bias == "NO_CLEAR_BIAS" else "No valid trigger yet",
    ]


# ─────────────────────────── async assembly ───────────────────────────


def _bars_for_timeframe(timeframe: str, intraday_1m: list[Bar], daily: list[Bar]) -> list[Bar]:
    minutes = TIMEFRAME_MINUTES[timeframe]
    if minutes is None:
        return daily
    return _aggregate(intraday_1m, minutes)


def _suggested_usage(timeframe: str, dominant_bias: str) -> str:
    """Section G's Scalp/Intraday/Both/Wait classification -- purely a
    function of which timeframe bucket the request itself falls into,
    matching the approved architecture's own timeframe table. WAIT
    whenever there's no clear bias regardless of timeframe -- a
    classification of "good for scalping" on a symbol with no real edge
    right now would itself be a fabricated recommendation."""
    if dominant_bias == "NO_CLEAR_BIAS":
        return "WAIT"
    if timeframe in ("3m", "5m"):
        return "SCALP"
    if timeframe == "15m":
        return "BOTH"
    return "INTRADAY"


def compute_structure_signal_from_bars(
    *,
    symbol: str,
    timeframe: str,
    htf: str,
    bars: list[Bar],
    htf_bars: list[Bar],
    daily_asof: list[Bar],
    intraday_1m_asof: list[Bar],
    include_vwap: bool,
    config: StructureSignalConfig,
) -> Payload:
    """The pure Phase 1/2 core -- Sections A through F, assembled purely
    from bar windows already handed to it. No Redis, no lookahead of its
    own: every argument must already be trimmed to "as of" whatever
    point is being evaluated.

    Extracted (Phase 3, 2026-08-29) out of compute_structure_signal()
    so the live route and the historical replay backtester share this
    ONE real implementation instead of two copies that could quietly
    diverge -- compute_structure_signal() below is now a thin Redis-
    fetching wrapper around this function; structure_backtest.py's own
    replay loop calls it directly with each step's own trimmed windows.
    `daily_asof`/`intraday_1m_asof` are separate from `bars`/`htf_bars`
    because squeeze/RVOL/session-VWAP are deliberately always computed
    from DAILY (or intraday-session) bars regardless of the requested
    primary timeframe, per this module's own established Phase 1/2
    design -- see compute_rvol/compute_squeeze_readiness's own callers
    below for exactly where that split matters for lookahead safety
    during a replay."""
    from api.smc_geometry import compute_smc_geometry

    if not bars:
        return {
            "ready": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "reason": "No historical bar history yet.",
        }

    pack: IndicatorPack | None = _indicators(bars, include_vwap)
    if pack is None:
        return {
            "ready": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "reason": "Not enough bars for indicators yet.",
        }

    geometry = compute_smc_geometry(bars)
    htf_geometry = compute_smc_geometry(htf_bars) if htf_bars else {"trend_state": 0}

    mfi = _mfi(bars)
    squeeze_readiness = compute_squeeze_readiness(daily_asof) if daily_asof else None
    rvol = compute_rvol(daily_asof) if daily_asof else None
    vwap = _vwap_session(intraday_1m_asof) if include_vwap else pack.vwap

    # bb_width_expanding: a real, cheap proxy from the SAME squeeze
    # readiness number rather than a second Bollinger pass -- a falling
    # readiness (bands widening back out relative to the Keltner
    # channel) between the last two available real daily bars means
    # volatility is expanding right now, not just "was compressed
    # earlier." None-safe: no signal either way when either side is
    # unavailable yet, never a fabricated True.
    bb_width_expanding = False
    if daily_asof and len(daily_asof) >= 22:
        prior_readiness = compute_squeeze_readiness(daily_asof[:-1])
        if prior_readiness is not None and squeeze_readiness is not None:
            bb_width_expanding = squeeze_readiness < prior_readiness

    reads = SubscoreReads(
        close=pack.close,
        ema200=pack.ema200,
        htf_trend_state=int(htf_geometry.get("trend_state") or 0),
        supertrend=pack.supertrend,
        rsi14=pack.rsi14,
        mfi=mfi,
        squeeze_readiness=squeeze_readiness,
        bb_width_expanding=bb_width_expanding,
        rvol=rvol,
        vwap=vwap,
    )
    bull_score, bear_score = compute_setup_scores(reads, config)
    dominant_bias = compute_dominant_bias(bull_score, bear_score, config)

    ltp = pack.close
    atr = pack.atr
    trigger: Trigger | None = None
    candle_confirmed = False
    risk: RiskLevels | None = None
    if dominant_bias != "NO_CLEAR_BIAS":
        bullish = dominant_bias == "BULLISH"
        trigger = select_breakout_trigger(
            bars=bars, geometry=geometry, atr=atr, ltp=ltp, bullish=bullish, config=config
        )
        if trigger is not None and atr is not None:
            candle_confirmed = candle_confirms(bars[-1], trigger.price, bullish, config)
            if candle_confirmed:
                structure_level = (
                    geometry.get("swing_low_1") if bullish else geometry.get("swing_high_1")
                )
                risk = compute_risk_levels(
                    entry=bars[-1]["close"],
                    structure_level=(
                        float(structure_level) if structure_level is not None else None
                    ),
                    atr=atr,
                    bullish=bullish,
                    config=config,
                )

    trade_readiness = compute_trade_readiness(
        dominant_bias=dominant_bias,
        bull_score=bull_score,
        bear_score=bear_score,
        config=config,
        trigger_present=trigger is not None,
        candle_confirmed=candle_confirmed,
    )
    momentum_watch = is_momentum_watch(ltp, trigger, atr) if not candle_confirmed else False
    travel_status = compute_travel_status(trade_readiness=trade_readiness)
    markers = compute_visual_markers(
        dominant_bias=dominant_bias,
        momentum_watch=momentum_watch,
        breakout_confirmed=candle_confirmed,
    )
    interpretation = build_interpretation_label(
        dominant_bias=dominant_bias,
        trigger=trigger,
        bull_score=bull_score,
        bear_score=bear_score,
        momentum_watch=momentum_watch,
        candle_confirmed=candle_confirmed,
    )

    return {
        "ready": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "htf_timeframe": htf,
        "htf_is_self": htf == timeframe,
        "ltp": round(ltp, 2),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "dominant_bias": dominant_bias,
        "trade_readiness": trade_readiness,
        "trigger_price": trigger.price if trigger else None,
        "trigger_side": trigger.side if trigger else None,
        "trigger_source": trigger.source if trigger else None,
        "momentum_watch": momentum_watch,
        "candle_confirmed": candle_confirmed,
        "entry": risk.entry if risk else None,
        "sl": risk.sl if risk else None,
        "tp1": risk.tp1 if risk else None,
        "tp2": risk.tp2 if risk else None,
        "tp3": risk.tp3 if risk else None,
        "risk_per_share": risk.risk_per_share if risk else None,
        "travel_status": travel_status,
        "visual_markers": markers,
        "interpretation_label": interpretation,
        "suggested_usage": _suggested_usage(timeframe, dominant_bias),
        "indicators": {
            "ema200": pack.ema200,
            "rsi14": pack.rsi14,
            "mfi": mfi,
            "supertrend": pack.supertrend,
            "vwap": vwap,
            "atr": atr,
            "squeeze_readiness": squeeze_readiness,
            "rvol": rvol,
            "htf_trend_state": reads.htf_trend_state,
        },
        "disclaimer": DISCLAIMER,
    }


async def compute_structure_signal(
    redis: Any,
    symbol: str,
    timeframe: str = "15m",
    config: StructureSignalConfig = DEFAULT_CONFIG,
) -> Payload:
    """Thin Redis-fetching wrapper around compute_structure_signal_from_bars()
    -- the full Phase 1/2 composite for one symbol/timeframe, live. See
    this module's own header for exactly what's reused vs. new."""
    symbol = symbol.upper().strip()
    timeframe = timeframe.lower().strip()
    if timeframe not in TIMEFRAME_MINUTES:
        return {
            "ready": False,
            "reason": f"Unsupported timeframe: {timeframe}. Use one of {sorted(TIMEFRAME_MINUTES)}.",
        }

    intraday_1m, daily, _nifty = await _load_bars(redis, symbol)
    bars = _bars_for_timeframe(timeframe, intraday_1m, daily)
    htf = _htf_for(timeframe)
    htf_bars = _bars_for_timeframe(htf, intraday_1m, daily)
    include_vwap = config.vwap_enabled and _is_intraday_timeframe(timeframe)

    return compute_structure_signal_from_bars(
        symbol=symbol,
        timeframe=timeframe,
        htf=htf,
        bars=bars,
        htf_bars=htf_bars,
        daily_asof=daily,
        intraday_1m_asof=intraday_1m,
        include_vwap=include_vwap,
        config=config,
    )


async def compute_structure_universe(
    redis: Any, timeframe: str = "15m", config: StructureSignalConfig = DEFAULT_CONFIG
) -> Payload:
    """Bulk bias/readiness/travel_status for the full symbol universe --
    a watchlist view, not the full per-symbol composite (that's GET
    /api/structure/signal, one symbol at a time). Computed per-request
    like GET /api/screener/structure already is, not a background
    hydration loop -- Phase 1/2's own explicit scope."""
    symbols = await _symbol_universe(redis)
    rows: Payload = {}
    for symbol in symbols:
        signal = await compute_structure_signal(redis, symbol, timeframe, config)
        if not signal.get("ready"):
            continue
        rows[symbol] = {
            "dominant_bias": signal["dominant_bias"],
            "trade_readiness": signal["trade_readiness"],
            "travel_status": signal["travel_status"],
            "bull_score": signal["bull_score"],
            "bear_score": signal["bear_score"],
            "trigger_price": signal["trigger_price"],
            "trigger_side": signal["trigger_side"],
            "suggested_usage": signal["suggested_usage"],
        }
    return {"count": len(rows), "timeframe": timeframe, "rows": rows, "disclaimer": DISCLAIMER}
