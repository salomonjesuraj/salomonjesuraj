"""Seed infusion:symbols Redis hash with mock instrument data.

Run before starting normalizer so it can resolve instrument_key -> symbol.
Matches the mock adapter's 5 symbols.

Usage:
    python scripts/seed_symbols.py [--redis-url redis://localhost:6379/0]
"""

import asyncio
import sys

import msgpack
from redis.asyncio import Redis

SYMBOLS = [
    {
        "instrument_key": "NSE_EQ|INE002A01018",
        "symbol": "RELIANCE",
        "isin": "INE002A01018",
        "sector_id": "NIFTY_50",
        "is_fno": True,
        "lot_size": 250,
        "tier": 1,
        "exchange": "NSE",
        "segment": "EQ",
    },
    {
        "instrument_key": "NSE_EQ|INE009A01021",
        "symbol": "INFY",
        "isin": "INE009A01021",
        "sector_id": "NIFTY_IT",
        "is_fno": True,
        "lot_size": 300,
        "tier": 1,
        "exchange": "NSE",
        "segment": "EQ",
    },
    {
        "instrument_key": "NSE_EQ|INE040A01034",
        "symbol": "HDFCBANK",
        "isin": "INE040A01034",
        "sector_id": "NIFTY_BANK",
        "is_fno": True,
        "lot_size": 550,
        "tier": 1,
        "exchange": "NSE",
        "segment": "EQ",
    },
    {
        "instrument_key": "NSE_EQ|INE467B01029",
        "symbol": "TCS",
        "isin": "INE467B01029",
        "sector_id": "NIFTY_IT",
        "is_fno": True,
        "lot_size": 175,
        "tier": 1,
        "exchange": "NSE",
        "segment": "EQ",
    },
    {
        "instrument_key": "NSE_INDEX|Nifty 50",
        "symbol": "NIFTY50",
        "isin": "",
        "sector_id": "INDEX",
        "is_fno": False,
        "lot_size": 50,
        "tier": 1,
        "exchange": "NSE",
        "segment": "INDEX",
    },
]


async def main():
    redis_url = "redis://localhost:6379/0"
    if "--redis-url" in sys.argv:
        idx = sys.argv.index("--redis-url")
        redis_url = sys.argv[idx + 1]

    redis = Redis.from_url(redis_url, decode_responses=False)
    await redis.ping()

    pipe = redis.pipeline()
    for sym in SYMBOLS:
        key = sym["instrument_key"]
        value = msgpack.packb(sym)
        pipe.hset("infusion:symbols", key, value)

    await pipe.execute()
    await redis.aclose()

    print(f"Seeded {len(SYMBOLS)} symbols to infusion:symbols")
    for s in SYMBOLS:
        print(
            f"  {s['instrument_key']} -> {s['symbol']} (tier={s['tier']}, sector={s['sector_id']})"
        )


if __name__ == "__main__":
    asyncio.run(main())
