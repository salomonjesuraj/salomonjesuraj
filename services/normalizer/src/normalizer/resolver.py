"""Symbol resolver — maps broker instrument_key to symbol metadata.

Supports resolution by:
  1. Upstox key:  NSE_EQ|INE002A01018  (ISIN-based, from infusion:symbols)
  2. Kite key:    NSE_EQ|RELIANCE      (symbol-based, from Kite adapter)
  3. Plain symbol: RELIANCE            (fallback)
"""

from dataclasses import dataclass

import msgpack
import structlog
from redis.asyncio import Redis

from infusion_streams.constants import KEY_SYMBOLS

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    symbol: str
    isin: str
    sector_id: str
    is_fno: bool
    lot_size: int
    tier: int              # 1, 2, or 3


class SymbolResolver:
    """
    Maps broker instrument_key -> SymbolInfo.
    Loaded from Redis at startup, reloaded on config version change.

    Builds multiple indices for O(1) lookup:
      - _by_upstox_key:  NSE_EQ|INE002A01018 -> SymbolInfo
      - _by_kite_key:    NSE_EQ|RELIANCE     -> SymbolInfo
      - _by_symbol:      RELIANCE            -> SymbolInfo
    """

    def __init__(self):
        self._by_upstox_key: dict[str, SymbolInfo] = {}
        self._by_kite_key: dict[str, SymbolInfo] = {}
        self._by_symbol: dict[str, SymbolInfo] = {}
        self._config_version: str = ""

    async def load(self, redis: Redis):
        """Load symbol master from Redis and build all indices."""
        raw = await redis.hgetall(KEY_SYMBOLS)
        by_upstox = {}
        by_kite = {}
        by_symbol = {}

        for key, value in raw.items():
            k = key.decode() if isinstance(key, bytes) else key
            info_raw = msgpack.unpackb(value, raw=False)
            symbol = info_raw.get("symbol", "")
            if not symbol:
                continue

            info = SymbolInfo(
                symbol=symbol,
                isin=info_raw.get("isin", ""),
                sector_id=info_raw.get("sector_id", "UNCATEGORIZED"),
                is_fno=info_raw.get("is_fno", False),
                lot_size=info_raw.get("lot_size", 1),
                tier=info_raw.get("tier", 3),
            )

            # Primary: Upstox key (e.g., NSE_EQ|INE002A01018)
            by_upstox[k] = info

            # Secondary: Kite-style key (e.g., NSE_EQ|RELIANCE)
            exchange = info_raw.get("exchange", "NSE")
            segment = info_raw.get("segment", "EQ")
            kite_key = f"{exchange}_{segment}|{symbol}"
            by_kite[kite_key] = info

            # Tertiary: plain symbol
            by_symbol[symbol] = info

        self._by_upstox_key = by_upstox
        self._by_kite_key = by_kite
        self._by_symbol = by_symbol

        logger.info(
            "symbols_loaded",
            upstox_keys=len(by_upstox),
            kite_keys=len(by_kite),
            symbols=len(by_symbol),
        )

    def resolve(self, instrument_key: str) -> SymbolInfo | None:
        """O(1) lookup across all key formats.

        Tries in order:
          1. Upstox format (NSE_EQ|INE002A01018)
          2. Kite format (NSE_EQ|RELIANCE)
          3. Plain symbol (RELIANCE)
        """
        info = self._by_upstox_key.get(instrument_key)
        if info:
            return info

        info = self._by_kite_key.get(instrument_key)
        if info:
            return info

        return self._by_symbol.get(instrument_key)

    @property
    def count(self) -> int:
        return len(self._by_symbol)
