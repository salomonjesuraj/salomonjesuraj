"""NSE F&O ban-list capture -- Phase 13.13 (folded into Phase 13.7).

NSE publishes a daily list of securities under F&O trading ban -- stocks
whose Market Wide Position Limit (MWPL) has hit >=95% of the exchange cap.
While a symbol is banned, SEBI/NSE rules forbid opening any NEW F&O
position in it (only unwinding existing positions is allowed). This is a
hard, published trading constraint, not a signal-quality judgment -- unlike
every other Phase 1-13.x field, it's deliberately wired as an actual
suppression gate (scanner/suppression.py) rather than left informational,
because "you cannot legally open this position today" isn't evidence for a
strategy to earn its way past -- it's the same category of hard stop as
market-closed or an invalid instrument.

Confirmed live (this session, 2026-08-12): unlike the delivery bhavcopy,
this endpoint needs no date in the filename (it's always "today's" list)
and returns real data with a plain GET, no cookie warmup needed at all.
"""

from __future__ import annotations

import re
from typing import Any

import aiohttp
import structlog

from nse_scraper.delivery import _HEADERS  # same realistic browser headers

logger = structlog.get_logger()
Payload = dict[str, Any]

FO_BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
_DATE_RE = re.compile(r"Trade Date\s+(\d{2}-[A-Z]{3}-\d{4})", re.IGNORECASE)


def parse_fo_ban_csv(text: str) -> tuple[str | None, set[str]]:
    """First line is a header ("Securities in Ban For Trade Date
    DD-MON-YYYY:"), remaining lines are "N,SYMBOL". An empty ban day still
    has the header with zero data rows -- that's valid, not a parse
    failure."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, set()

    trade_date_match = _DATE_RE.search(lines[0])
    trade_date = trade_date_match.group(1) if trade_date_match else None

    symbols: set[str] = set()
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) >= 2:
            symbol = parts[1].strip()
            if symbol:
                symbols.add(symbol)
    return trade_date, symbols


async def fetch_fo_ban(session: aiohttp.ClientSession) -> tuple[str | None, set[str]] | None:
    try:
        async with session.get(
            FO_BAN_URL, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
    except Exception as exc:
        logger.warning("nse_fo_ban_fetch_error", error=str(exc))
        return None
    if "ban" not in text.lower()[:60]:
        return None
    return parse_fo_ban_csv(text)


async def run_fo_ban_capture(redis: Any) -> Payload:
    """Fetch the current F&O ban list and replace
    infusion:nse:fo_ban:symbols (a Redis SET) atomically -- always a full
    delete+recreate, not an incremental add, so a symbol that comes off
    the ban list actually leaves the set rather than lingering."""
    async with aiohttp.ClientSession() as session:
        result = await fetch_fo_ban(session)

    if result is None:
        return {"status": "fetch_failed"}

    trade_date, symbols = result
    key = "infusion:nse:fo_ban:symbols"
    pipe = redis.pipeline(transaction=False)
    pipe.delete(key)
    if symbols:
        pipe.sadd(key, *symbols)
    pipe.expire(
        key, 20 * 3600
    )  # spans one trading session with margin, never carries into a stale next day
    if trade_date:
        pipe.set("infusion:nse:fo_ban:trade_date", trade_date, ex=20 * 3600)
    await pipe.execute()

    logger.info(
        "fo_ban_capture_complete",
        trade_date=trade_date,
        banned_count=len(symbols),
        symbols=sorted(symbols),
    )
    return {"status": "complete", "trade_date": trade_date, "banned_count": len(symbols)}
