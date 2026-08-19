"""Mock broker adapter — generates realistic fake tick data for development/testing."""

import asyncio
import random
import time

import structlog

from ingestion.adapters.base import BrokerAdapter, ConnectionState
from infusion_models.tick import RawTickV1
from infusion_common.timing import now_us

logger = structlog.get_logger()

# 5 representative instruments for initial testing
MOCK_SYMBOLS = [
    ("NSE_EQ|INE002A01018", "NSE", "EQ", "RELIANCE",  2500.0),
    ("NSE_EQ|INE009A01021", "NSE", "EQ", "INFY",      1500.0),
    ("NSE_EQ|INE040A01034", "NSE", "EQ", "HDFCBANK",  1600.0),
    ("NSE_EQ|INE467B01029", "NSE", "EQ", "TCS",       3500.0),
    ("NSE_INDEX|Nifty 50",  "NSE", "INDEX", "NIFTY50", 22000.0),
]


class MockAdapter(BrokerAdapter):
    """Generates fake tick data for development/testing."""

    name = "mock"

    def __init__(self, config):
        self.config = config
        self.state = ConnectionState.INIT
        self._prices: dict[str, float] = {}
        self._volumes: dict[str, int] = {}
        self._tick_count = 0
        self._start_time = 0.0

    async def authenticate(self) -> str:
        self.state = ConnectionState.AUTHENTICATING
        logger.info("mock_authenticated")
        return "mock-token"

    async def connect(self) -> None:
        self.state = ConnectionState.CONNECTING
        self._start_time = time.time()
        # Initialize mock prices and volumes
        for key, _, _, name, base_price in MOCK_SYMBOLS:
            self._prices[key] = base_price
            self._volumes[key] = random.randint(100_000, 5_000_000)
        logger.info("mock_connected", symbols=len(MOCK_SYMBOLS))

    async def subscribe(self, instrument_keys: list[str]) -> None:
        self.state = ConnectionState.SUBSCRIBING
        self.state = ConnectionState.STREAMING
        logger.info("mock_subscribed", count=len(MOCK_SYMBOLS))

    async def unsubscribe(self, instrument_keys: list[str]) -> None:
        logger.info("mock_unsubscribed", count=len(instrument_keys))

    async def change_mode(self, instrument_keys: list[str], mode: str) -> None:
        logger.info("mock_mode_changed", count=len(instrument_keys), mode=mode)

    async def start_streaming(self, on_tick) -> None:
        self.state = ConnectionState.STREAMING
        interval = 1.0 / max(self.config.mock_tick_rate_hz, 1)

        while self.state == ConnectionState.STREAMING:
            for key, exchange, segment, name, _ in MOCK_SYMBOLS:
                price = self._prices[key]
                # Random walk: ±0.1% per tick
                change = price * random.uniform(-0.001, 0.001)
                price = round(price + change, 2)
                self._prices[key] = price

                # Volume increases monotonically during session
                vol = self._volumes[key]
                vol += random.randint(100, 5000)
                self._volumes[key] = vol

                prev_close = round(price * random.uniform(0.99, 1.01), 2)
                day_open = round(price * random.uniform(0.995, 1.005), 2)
                day_high = round(max(price * 1.01, price + random.uniform(0, 5)), 2)
                day_low = round(min(price * 0.99, price - random.uniform(0, 5)), 2)

                tick = RawTickV1(
                    broker="mock",
                    instrument_key=key,
                    exchange=exchange,
                    segment=segment,
                    ltp=price,
                    open=day_open,
                    high=day_high,
                    low=day_low,
                    close=prev_close,
                    volume=vol,
                    oi=random.randint(0, 100000) if segment == "FO" else 0,
                    total_buy_qty=random.randint(5000, 50000),
                    total_sell_qty=random.randint(5000, 50000),
                    best_bid=round(price - 0.05, 2),
                    best_ask=round(price + 0.05, 2),
                    best_bid_qty=random.randint(100, 5000),
                    best_ask_qty=random.randint(100, 5000),
                    exchange_timestamp_ms=int(time.time() * 1000),
                    received_at_us=now_us(),
                )
                await on_tick(tick)
                self._tick_count += 1

            await asyncio.sleep(interval)

    async def disconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED
        logger.info("mock_disconnected", total_ticks=self._tick_count)

    def health(self) -> dict:
        return {
            "broker": "mock",
            "state": self.state.value,
            "tick_count": self._tick_count,
            "last_tick_age_ms": -1,
            "reconnect_count": 0,
        }
