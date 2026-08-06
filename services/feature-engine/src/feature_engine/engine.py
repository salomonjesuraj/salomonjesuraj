"""Feature engine orchestrator — micro-batch processing of normalized ticks."""

import asyncio
from datetime import datetime, timezone, timedelta

import structlog

from feature_engine.state import SymbolState, OHLCBar
from feature_engine.bar_builder import update_bars
from feature_engine.features.price import (
    update_price_features, update_ema_features, get_vwap, get_gap_pct, get_change_pct,
)
from feature_engine.features.momentum import (
    update_rsi, get_rsi, update_macd, get_macd,
    update_stochastic, get_stochastic, update_cci, get_cci,
)
from feature_engine.features.volatility import update_atr, update_bollinger, get_bollinger
from feature_engine.features.volume import update_obv, get_relative_volume, get_volume_sma
from feature_engine.features.microstructure import get_spread_bps, get_order_imbalance
from infusion_models.feature import FeatureVectorV1
from infusion_common.timing import now_us

logger = structlog.get_logger()


def _bar_dict(bar) -> dict:
    """Compact dict used for lightweight pattern checks."""
    return {
        "o": float(bar.open),
        "h": float(bar.high),
        "l": float(bar.low),
        "c": float(bar.close),
        "v": int(bar.volume),
    }


def _range(bar: dict) -> float:
    return max(0.0, float(bar.get("h", 0.0)) - float(bar.get("l", 0.0)))


def _body(bar: dict) -> float:
    return abs(float(bar.get("c", 0.0)) - float(bar.get("o", 0.0)))


def _detect_nr_pattern(bars) -> str:
    """Detect NR4/NR7 style range contraction on the latest 1m bar."""
    items = list(bars)
    if len(items) < 4:
        return ""
    ranges = [_range(b) for b in items]
    latest = ranges[-1]
    if latest <= 0:
        return ""
    if len(ranges) >= 7 and latest <= min(ranges[-7:]):
        return "NR7"
    if latest <= min(ranges[-4:]):
        return "NR4"
    return ""


def _detect_candle_pattern(bars) -> str:
    """Small, dependency-free subset of high-value candle labels."""
    items = list(bars)
    if not items:
        return ""
    cur = items[-1]
    o, h, l, c = (float(cur.get(k, 0.0)) for k in ("o", "h", "l", "c"))
    rng = max(h - l, 0.0)
    if rng <= 0:
        return ""
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if len(items) >= 2:
        prev = items[-2]
        po, pc, ph, pl = (float(prev.get(k, 0.0)) for k in ("o", "c", "h", "l"))
        if h <= ph and l >= pl:
            return "Inside Bar"
        if pc < po and c > o and c >= po and o <= pc:
            return "Bullish Engulfing"
        if pc > po and c < o and c <= po and o >= pc:
            return "Bearish Engulfing"
    if body <= rng * 0.12:
        return "Doji"
    if lower >= body * 2.0 and upper <= body * 0.8 and c >= o:
        return "Hammer"
    if upper >= body * 2.0 and lower <= body * 0.8 and c <= o:
        return "Shooting Star"
    return ""


def _squeeze_state(bb_width: float) -> str:
    if bb_width <= 0:
        return "NA"
    if bb_width <= 0.008:
        return "EXTREME"
    if bb_width <= 0.012:
        return "COILED"
    if bb_width <= 0.018:
        return "BUILDING"
    if bb_width >= 0.025:
        return "EXPANDING"
    return "NORMAL"


def _atr_trail(ltp: float, atr: float, ema20: float, macd_hist: float) -> tuple[float, str]:
    """Lightweight ATR trailing-stop proxy inspired by UT/ATR trail scanners."""
    if ltp <= 0 or atr <= 0:
        return 0.0, "NEUTRAL"
    if ltp > ema20 > 0 and macd_hist >= 0:
        return max(0.0, ltp - atr * 1.2), "BULL"
    if ltp < ema20 and ema20 > 0 and macd_hist <= 0:
        return ltp + atr * 1.2, "BEAR"
    return 0.0, "NEUTRAL"


class FeatureEngine:
    """
    Orchestrates feature computation with micro-batching.

    Flow:
      1. Ticks arrive and are buffered
      2. Every 5ms OR when buffer hits 200: flush
      3. Group ticks by symbol (only latest tick per symbol in batch matters)
      4. Compute features for each symbol
      5. Emit FeatureVector
    """

    def __init__(self, config):
        self.config = config
        self._states: dict[str, SymbolState] = {}
        self._buffer: list[dict] = []
        self._lock = asyncio.Lock()
        self._on_feature = None  # callback
        self._on_bar = None
        self._profile_loader = None
        self._history_loader = None
        self._processed = 0

    def set_callback(self, fn):
        """Set callback for emitting feature vectors."""
        self._on_feature = fn

    def set_bar_callback(self, fn):
        self._on_bar = fn

    def set_volume_profile_loader(self, fn):
        self._profile_loader = fn

    def set_history_loader(self, fn):
        self._history_loader = fn

    async def ingest(self, payload: dict):
        """Buffer a normalized tick for batch processing."""
        self._buffer.append(payload)
        if len(self._buffer) >= self.config.batch_max_ticks:
            await self._flush()

    async def flush_timer(self):
        """Background timer that flushes buffer every batch_timer_ms."""
        interval = self.config.batch_timer_ms / 1000.0
        while True:
            await asyncio.sleep(interval)
            if self._buffer:
                await self._flush()

    async def _flush(self):
        """Process buffered ticks."""
        async with self._lock:
            if not self._buffer:
                return

            batch = self._buffer
            self._buffer = []

        # Deduplicate: keep only latest tick per symbol
        latest_by_symbol: dict[str, dict] = {}
        for tick in batch:
            symbol = tick.get("symbol", "")
            latest_by_symbol[symbol] = tick

        # Compute features for each symbol
        for symbol, tick in latest_by_symbol.items():
            state = await self._get_or_create_state(symbol)
            feature, completed = self._compute(state, tick)
            if self._on_bar:
                for timeframe, bar in completed:
                    await self._on_bar(symbol, timeframe, bar)
            if feature and self._on_feature:
                await self._on_feature(feature)
                self._processed += 1

    async def _get_or_create_state(self, symbol: str) -> SymbolState:
        if symbol not in self._states:
            state = SymbolState(symbol=symbol)
            if self._profile_loader:
                profile = await self._profile_loader(symbol)
                if profile:
                    state.volume_profile = profile
                    state.volume_profile_ready = True
            self._states[symbol] = state
        state = self._states[symbol]
        if (not state.history_seeded and state.completed_1m_bars == 0
                and self._history_loader
                and now_us() - state.history_seed_checked_us > 300_000_000):
            state.history_seed_checked_us = now_us()
            bars = await self._history_loader(symbol)
            if bars:
                for bar in bars[-60:]:
                    close = float(bar["c"])
                    update_ema_features(state, close)
                    update_rsi(state, close, self.config.rsi_period)
                    update_macd(state, close, self.config.macd_fast, self.config.macd_slow, self.config.macd_signal)
                    update_atr(state, float(bar["h"]), float(bar["l"]), close, self.config.atr_period)
                    update_bollinger(state, close)
                    update_stochastic(state, float(bar["h"]), float(bar["l"]), close)
                    update_cci(state, float(bar["h"]), float(bar["l"]), close, self.config.cci_period)
                    update_obv(state, close, int(bar.get("v", 0)))
                    state.volume_history.append(int(bar.get("v", 0)))
                    state.recent_1m_bars.append({
                        "o": float(bar.get("o", close)),
                        "h": float(bar["h"]),
                        "l": float(bar["l"]),
                        "c": close,
                        "v": int(bar.get("v", 0)),
                    })
                    state.completed_1m_bars += 1
                state.history_seeded = True
        if (not state.volume_profile_ready and self._profile_loader
                and now_us() - state.volume_profile_checked_us > 300_000_000):
            state.volume_profile_checked_us = now_us()
            profile = await self._profile_loader(symbol)
            if profile:
                state.volume_profile = profile
                state.volume_profile_ready = True
        return state

    def _compute(self, state: SymbolState, tick: dict) -> tuple[FeatureVectorV1 | None, list]:
        """Run all feature computations for a symbol."""
        ltp = tick.get("ltp", 0.0)
        if ltp <= 0:
            return None, []

        volume = tick.get("volume", 0)
        high = tick.get("high", ltp)
        low = tick.get("low", ltp)
        close = tick.get("close", ltp)  # prev day close

        exchange_ms = int(tick.get("exchange_timestamp_ms", 0) or 0)
        if exchange_ms <= 0:
            exchange_ms = int(now_us() / 1000)
        ist = timezone(timedelta(minutes=330))
        session_date = datetime.fromtimestamp(exchange_ms / 1000, tz=timezone.utc).astimezone(ist).date().isoformat()

        # Kite volume is cumulative for the session. Only its positive delta is
        # valid tick/bar volume. A restart starts from zero delta to avoid
        # assigning the entire day's volume to one candle.
        if state.session_date != session_date:
            state.session_date = session_date
            state.vwap_numerator = 0.0
            state.vwap_denominator = 0
            state.day_high = 0.0
            state.day_low = float("inf")
            state.day_open = tick.get("open", ltp) or ltp
            state.bar_1m = OHLCBar()
            state.bar_5m = OHLCBar()
            state.bar_15m = OHLCBar()
            state.previous_cumulative_volume = volume
            state.session_cumulative_volume = volume
            volume_delta = 0
        elif state.previous_cumulative_volume <= 0:
            volume_delta = 0
            state.previous_cumulative_volume = volume
            state.session_cumulative_volume = volume
        elif volume >= state.previous_cumulative_volume:
            volume_delta = volume - state.previous_cumulative_volume
            state.previous_cumulative_volume = volume
            state.session_cumulative_volume = volume
        else:
            # Exchange reset/correction: never publish negative volume.
            volume_delta = 0
            state.previous_cumulative_volume = volume
            state.session_cumulative_volume = volume

        # Update state from tick
        state.ltp = ltp
        state.volume = volume
        state.oi = tick.get("oi", 0)
        state.best_bid = tick.get("best_bid", 0.0)
        state.best_ask = tick.get("best_ask", 0.0)
        state.total_buy_qty = tick.get("best_bid_qty", 0)
        state.total_sell_qty = tick.get("best_ask_qty", 0)
        state.last_tick_exchange_ms = exchange_ms

        if state.prev_close == 0 and close > 0:
            state.prev_close = close
        if state.day_open == 0:
            state.day_open = tick.get("open", ltp)

        # VWAP/day state remains live. Technical indicators advance only from
        # completed 1-minute OHLCV bars so tick rate cannot distort them.
        update_price_features(state, ltp, volume)
        completed = update_bars(state, ltp, volume_delta, state.last_tick_exchange_ms)
        completed_1m = next((bar for tf, bar in completed if tf == 1), None)
        if completed_1m is not None:
            close_1m = completed_1m.close
            update_ema_features(state, close_1m)
            update_rsi(state, close_1m, self.config.rsi_period)
            update_macd(state, close_1m, self.config.macd_fast, self.config.macd_slow, self.config.macd_signal)
            update_atr(state, completed_1m.high, completed_1m.low, close_1m, self.config.atr_period)
            update_bollinger(state, close_1m)
            update_stochastic(state, completed_1m.high, completed_1m.low, close_1m)
            update_cci(state, completed_1m.high, completed_1m.low, close_1m, self.config.cci_period)
            update_obv(state, close_1m, completed_1m.volume)
            state.volume_history.append(completed_1m.volume)
            state.recent_1m_bars.append(_bar_dict(completed_1m))
            state.completed_1m_bars += 1
            state.last_completed_1m_ms = completed_1m.bar_start_ms

        # Collect computed features
        macd_line, macd_sig, macd_hist = get_macd(state)
        bb_upper, bb_lower, bb_width = get_bollinger(state)
        stoch_k, stoch_d = get_stochastic(state)
        atr_trail_stop, atr_trend = _atr_trail(
            ltp=ltp,
            atr=state.atr,
            ema20=state.ema.get(20, ltp),
            macd_hist=macd_hist,
        )

        return FeatureVectorV1(
            symbol=state.symbol,
            timestamp_us=now_us(),
            ltp=ltp,
            vwap=get_vwap(state),
            gap_pct=get_gap_pct(state),
            day_high=state.day_high,
            day_low=state.day_low if state.day_low != float("inf") else ltp,
            prev_close=state.prev_close,
            change_pct=get_change_pct(state),
            ema_5=state.ema.get(5, ltp),
            ema_9=state.ema.get(9, ltp),
            ema_20=state.ema.get(20, ltp),
            ema_50=state.ema.get(50, ltp),
            atr_14=state.atr,
            atr_trail_stop=atr_trail_stop,
            atr_trend=atr_trend,
            bb_upper=bb_upper,
            bb_lower=bb_lower,
            bb_width=bb_width,
            squeeze_state=_squeeze_state(bb_width),
            nr_pattern=_detect_nr_pattern(state.recent_1m_bars),
            candle_pattern=_detect_candle_pattern(state.recent_1m_bars),
            rsi_14=get_rsi(state),
            macd=macd_line,
            macd_signal=macd_sig,
            macd_hist=macd_hist,
            stochastic_k=stoch_k,
            stochastic_d=stoch_d,
            cci_20=get_cci(state),
            rel_vol_20d=get_relative_volume(state),
            obv=state.obv,
            volume_sma_20=get_volume_sma(state),
            volume_profile_ready=state.volume_profile_ready,
            bar_closed_1m=completed_1m is not None,
            indicator_ready=state.completed_1m_bars >= max(self.config.bb_period, self.config.macd_slow),
            completed_1m_bars=state.completed_1m_bars,
            spread_bps=get_spread_bps(state),
            order_imbalance=get_order_imbalance(state),
        ), completed

    @property
    def stats(self) -> dict:
        return {
            "symbols_tracked": len(self._states),
            "features_computed": self._processed,
        }
