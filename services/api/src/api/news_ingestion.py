"""EBIE EB-7 (increment 1) -- Upstox News API ingestion: fetch, dedupe,
entity (symbol) mapping. Pure functions, no I/O beyond the one HTTP
call each takes explicitly -- verified in isolation the same way
api/futures.py and api/options_analytics_v2.py were.

Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q7.2, verified against Upstox's
own current API documentation before writing this (not guessed):

    GET https://api.upstox.com/v2/news?category=instrument_keys
        &instrument_keys=<comma-separated, max 30>
        &page_number=<1-100, default 1>&page_size=<1-100, default 100>

    Response: {"status": ..., "data": {"<instrument_key>": [
        {"heading", "summary", "thumbnail", "article_link",
         "published_time"} , ...
    ]}, "metadata": {"page": {...}}}

Note this is v2, not the v3 base every other Upstox call in this
codebase uses (market.py's UPSTOX_API_BASE) -- News API is documented
separately and hasn't moved to v3.

Explicitly out of scope for increment 1, disclosed rather than silently
half-built: pagination beyond page 1 (the 7-day recency window makes a
single page_size=100 request per instrument-key-batch sufficient for
real observed volume; a symbol genuinely publishing >100 articles in
7 days would need page 2+, not implemented here) and any
classification/sentiment (that is EB-7 increment 2's sentiment-engine
service, per the authorized contract in Q4.2 -- this module only
fetches, dedupes, and entity-maps).
"""

from __future__ import annotations

import hashlib
from typing import Any

import aiohttp

NEWS_API_BASE = "https://api.upstox.com/v2/news"
MAX_INSTRUMENT_KEYS_PER_REQUEST = 30
DEFAULT_PAGE_SIZE = 100
Payload = dict[str, Any]


def chunk_instrument_keys(
    instrument_keys: list[str], size: int = MAX_INSTRUMENT_KEYS_PER_REQUEST
) -> list[list[str]]:
    """Split a full-universe instrument-key list into <=30-key batches,
    per the News API's documented per-request cap."""
    return [instrument_keys[i : i + size] for i in range(0, len(instrument_keys), size)]


def fingerprint_article(article: Payload) -> str:
    """Stable identity for a real-world article, independent of which
    symbol it's attached to (used together with symbol as the dedup
    key -- see migrations/008_news_events.sql's UNIQUE(symbol,
    article_fingerprint)). Prefers article_link (closest thing to a
    canonical article identity Upstox's response offers); falls back to
    heading+published_time when article_link is missing/empty, which
    has been observed to happen for some real articles."""
    link = str(article.get("article_link") or "").strip()
    if link:
        basis = f"link:{link}"
    else:
        heading = str(article.get("heading") or "").strip()
        published = str(article.get("published_time") or "").strip()
        basis = f"heading_time:{heading}|{published}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


async def fetch_news_batch(
    session: aiohttp.ClientSession,
    access_token: str,
    instrument_keys: list[str],
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_sec: int = 10,
) -> Payload:
    """One real HTTP call for up to 30 instrument keys. Returns the raw
    {instrument_key: [article, ...]} data dict on success, {} on any
    failure (auth missing, non-200, network error, malformed body) --
    a failed batch is a gap for this sweep, not a crash; the next
    60s sweep will retry the same keys."""
    if not instrument_keys or not access_token:
        return {}
    keys = instrument_keys[:MAX_INSTRUMENT_KEYS_PER_REQUEST]
    params = {
        "category": "instrument_keys",
        "instrument_keys": ",".join(keys),
        "page_number": "1",
        "page_size": str(page_size),
    }
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    try:
        async with session.get(
            NEWS_API_BASE,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            if resp.status != 200:
                return {}
            payload = await resp.json()
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def map_articles_to_events(raw_data: Payload, inst_to_symbol: dict[str, str]) -> list[Payload]:
    """Flatten Upstox's {instrument_key: [article, ...]} response into
    one record per (symbol, article) pair, ready for a news_events
    insert. Entity mapping is a straight instrument_key -> symbol
    lookup against the live universe (same infusion:symbols map every
    other queue in this codebase already reads) -- an instrument_key
    the universe doesn't recognize is silently skipped (stale/delisted
    key echoed back by Upstox), not inserted with a null symbol."""
    events: list[Payload] = []
    for inst_key, articles in raw_data.items():
        symbol = inst_to_symbol.get(str(inst_key))
        if not symbol or not isinstance(articles, list):
            continue
        for article in articles:
            if not isinstance(article, dict):
                continue
            heading = str(article.get("heading") or "").strip()
            if not heading:
                continue
            events.append(
                {
                    "symbol": symbol,
                    "instrument_key": inst_key,
                    "article_fingerprint": fingerprint_article(article),
                    "heading": heading,
                    "summary": article.get("summary") or None,
                    "article_link": article.get("article_link") or None,
                    "thumbnail": article.get("thumbnail") or None,
                    "published_time_ms": _safe_int(article.get("published_time")),
                }
            )
    return events


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
