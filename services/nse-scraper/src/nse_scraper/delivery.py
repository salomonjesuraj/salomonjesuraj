"""NSE delivery % capture -- Phase 13.5.

NSE publishes a daily "Full Bhavcopy and Security Deliverable Data" CSV per
trading session (sec_bhavdata_full_DDMMYYYY.csv) containing, per symbol,
total traded quantity and delivered quantity/percentage -- the standard
India-specific institutional-conviction signal (high delivery % = shares
changing hands for genuine possession, not intraday churn) that Infusion has
had a schema placeholder for since the original design (see
FeatureVectorV1.delivery_pct, migrations/init.sql's ohlcv_daily.delivery_pct)
but never actually populated.

There is no live intraday delivery feed -- NSE only knows delivery quantity
after a session settles, published that evening. So this is fundamentally a
next-day signal: today's fetch captures T's (the just-closed session's)
delivery %, consumed by feature-engine as context throughout T+1's session
(matches the original design doc's own "feature-engine (next day warmup)"
note in docs/PHASE-2-MARKET-DATA-NSE-ENGINE.md).

NSE's site actively blocks scripted access without realistic browser headers
and session cookies (confirmed via this session's research into
ratan00/nse-rs's approach) -- a plain GET to the archives URL 403s. The
fetch below establishes cookies against the main site first, exactly as
that reference implementation does, then reuses them for the archives CSV.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta
from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger()
Payload = dict[str, Any]
DeliveryRows = dict[str, Payload]

NSE_HOME = "https://www.nseindia.com/"
NSE_ARCHIVES_BASE = "https://nsearchives.nseindia.com"
DELIVERY_MAX_LOOKBACK_DAYS = 7  # walk back over weekends/holidays until a real file is found
DELIVERY_HISTORY_MAX_DAYS = 20  # rolling window for the informational 20d average

# NSE 403s any request that doesn't look like a real browser -- no bearer
# token or API key exists for this public data, headers are the only gate.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _nse_cookies(session: aiohttp.ClientSession) -> None:
    """Best-effort warm of the session's cookie jar against NSE's homepage.

    Verified live against the real site (this session, 2026-08): the
    homepage itself 403s these plain-browser headers (NSE's bot protection
    on www.nseindia.com is stricter than assumed from ratan00/nse-rs's
    README alone), but nsearchives.nseindia.com -- the actual archive CSV
    endpoint below -- returns real 200s with no cookie at all. So this is
    not a hard prerequisite: a failure here is logged but never blocks the
    archive fetch, which is confirmed to work independently.
    """
    try:
        async with session.get(NSE_HOME, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=15)):
            pass
    except Exception as exc:
        logger.info("nse_cookie_warmup_skipped", error=str(exc))


async def _fetch_bhavdata_csv(session: aiohttp.ClientSession, trade_date: date) -> str | None:
    url = f"{NSE_ARCHIVES_BASE}/products/content/sec_bhavdata_full_{trade_date.strftime('%d%m%Y')}.csv"
    try:
        async with session.get(
            url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
            # NSE returns a 200 with an HTML error/redirect page for dates
            # with no data (weekends inside the walked-back range, etc.)
            # rather than a clean 404 -- a real bhavcopy CSV starts with the
            # SYMBOL header, so use that to distinguish.
            if not text.lstrip().upper().startswith("SYMBOL"):
                return None
            return text
    except Exception as exc:
        logger.warning(
            "nse_bhavdata_fetch_error", trade_date=trade_date.isoformat(), error=str(exc)
        )
        return None


def _parse_delivery_csv(csv_text: str) -> DeliveryRows:
    """SYMBOL -> {delivery_qty, delivery_pct}, EQ series only. NSE's CSV
    columns carry leading spaces (" DELIV_QTY", " DELIV_PER" etc.) -- this
    is a documented quirk of this specific file, not a parsing bug."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return {}
    fields = {name.strip(): name for name in reader.fieldnames}
    symbol_col = fields.get("SYMBOL")
    series_col = fields.get("SERIES")
    qty_col = fields.get("DELIV_QTY")
    pct_col = fields.get("DELIV_PER")
    if not (symbol_col and series_col and qty_col and pct_col):
        logger.warning("nse_bhavdata_unexpected_columns", columns=list(fields))
        return {}

    out: DeliveryRows = {}
    for row in reader:
        series = (row.get(series_col) or "").strip()
        if series != "EQ":
            continue
        symbol = (row.get(symbol_col) or "").strip()
        pct_raw = (row.get(pct_col) or "").strip()
        qty_raw = (row.get(qty_col) or "").strip()
        if not symbol or pct_raw in ("", "-"):
            continue
        try:
            out[symbol] = {
                "delivery_qty": int(float(qty_raw)) if qty_raw not in ("", "-") else 0,
                "delivery_pct": round(float(pct_raw), 2),
            }
        except ValueError:
            continue
    return out


async def fetch_latest_delivery(
    session: aiohttp.ClientSession,
) -> tuple[date, DeliveryRows] | None:
    """Walks back from today over weekends/holidays/not-yet-published days
    until a real bhavcopy is found. Returns (the session date the data is
    for, {symbol: {delivery_qty, delivery_pct}}), or None if nothing in the
    lookback window is available (e.g. NSE changed the URL format -- fails
    loudly via the caller's logging, not silently)."""
    await _nse_cookies(session)

    today = date.today()
    for offset in range(DELIVERY_MAX_LOOKBACK_DAYS):
        candidate = today - timedelta(days=offset)
        if candidate.weekday() >= 5:  # Saturday/Sunday -- NSE never publishes
            continue
        text = await _fetch_bhavdata_csv(session, candidate)
        if text:
            parsed = _parse_delivery_csv(text)
            if parsed:
                return candidate, parsed
    return None


async def _rolling_avg(
    redis: Any, symbol: str, trade_date: date, pct: float
) -> tuple[float | None, int]:
    """Append today's delivery_pct to the symbol's rolling history (capped
    at DELIVERY_HISTORY_MAX_DAYS entries) and return (average, sample_count).
    Average is None until there's at least one real sample -- never
    fabricated, and honestly reports how many days it's actually averaging
    over rather than silently implying a full 20-day window."""
    key = f"infusion:nse:delivery:history:{symbol}"
    raw = await redis.lrange(key, 0, 0)
    if raw:
        try:
            head = json.loads(raw[0].decode() if isinstance(raw[0], bytes) else raw[0])
            if head.get("date") == trade_date.isoformat():
                # Already recorded today's session -- avoid double-counting
                # if this job runs more than once before the date rolls over.
                all_raw = await redis.lrange(key, 0, DELIVERY_HISTORY_MAX_DAYS - 1)
                values = [
                    json.loads(r.decode() if isinstance(r, bytes) else r)["pct"] for r in all_raw
                ]
                return (round(sum(values) / len(values), 2) if values else None), len(values)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    entry = json.dumps({"date": trade_date.isoformat(), "pct": pct}, separators=(",", ":"))
    pipe = redis.pipeline(transaction=False)
    pipe.lpush(key, entry)
    pipe.ltrim(key, 0, DELIVERY_HISTORY_MAX_DAYS - 1)
    pipe.expire(key, 40 * 86400)  # generous vs. the 20-sample window; a gap doesn't wipe history
    await pipe.execute()

    all_raw = await redis.lrange(key, 0, DELIVERY_HISTORY_MAX_DAYS - 1)
    values = [json.loads(r.decode() if isinstance(r, bytes) else r)["pct"] for r in all_raw]
    return (round(sum(values) / len(values), 2) if values else None), len(values)


async def run_delivery_capture(redis: Any, universe_symbols: set[str]) -> Payload:
    """Fetch the latest available NSE delivery bhavcopy, store per-symbol
    delivery_qty/delivery_pct/avg_delivery_pct_20d for every symbol in
    Infusion's active universe (skips the ~2000 other NSE symbols the
    bhavcopy carries -- Infusion only tracks its configured tier), gated by
    "already have this session's data" so re-running this on a short
    interval is a cheap no-op rather than a redundant fetch+parse every time.
    """
    latest_key = "infusion:nse:delivery:latest_date"
    stored_date = await redis.get(latest_key)
    stored_date = stored_date.decode() if isinstance(stored_date, bytes) else stored_date

    async with aiohttp.ClientSession() as session:
        result = await fetch_latest_delivery(session)

    if result is None:
        return {"status": "unavailable", "reason": "no bhavcopy found in lookback window"}

    trade_date, parsed = result

    # Real bug caught live (this session): the old version short-circuited
    # the ENTIRE run -- including every per-symbol write -- the moment
    # `latest_date` matched, with no regard for which symbols actually got
    # written. A test run against a 4-symbol subset set latest_date once,
    # then permanently starved every subsequent real 208-symbol universe
    # run for the rest of that trading day (confirmed: only the original 4
    # symbols ever had real Redis data while the other 204 silently sat at
    # their SymbolState default of 0.0, indistinguishable in the dashboard
    # from a genuine 0% delivery day). Per-symbol writes are already
    # naturally idempotent (a plain HSET overwrite, and _rolling_avg's own
    # head-of-list date check prevents double-counting the same session in
    # the 20-day rolling average) -- so the fix is simply to stop skipping
    # them. The one-time cost of always re-fetching+re-parsing the CSV
    # (once per DELIVERY_CAPTURE_INTERVAL_SEC, a few seconds) is trivial
    # next to actually covering the universe correctly.
    already_current = stored_date == trade_date.isoformat()

    written = 0
    for symbol in universe_symbols:
        row = parsed.get(symbol)
        if not row:
            continue
        avg_pct, avg_days = await _rolling_avg(redis, symbol, trade_date, row["delivery_pct"])
        hash_key = f"infusion:nse:delivery:{symbol}"
        mapping = {
            "delivery_qty": str(row["delivery_qty"]),
            "delivery_pct": str(row["delivery_pct"]),
            "trade_date": trade_date.isoformat(),
            "avg_delivery_pct_20d": str(avg_pct) if avg_pct is not None else "",
            "avg_days": str(avg_days),
        }
        pipe = redis.pipeline(transaction=False)
        pipe.hset(hash_key, mapping=mapping)
        pipe.expire(hash_key, 3 * 86400)  # survives a long weekend, not indefinitely stale
        await pipe.execute()
        written += 1

    await redis.set(latest_key, trade_date.isoformat(), ex=3 * 86400)
    logger.info(
        "delivery_capture_complete",
        trade_date=trade_date.isoformat(),
        symbols_in_bhavcopy=len(parsed),
        universe_matched=written,
        was_already_current=already_current,
    )
    return {
        "status": "complete",
        "trade_date": trade_date.isoformat(),
        "symbols_in_bhavcopy": len(parsed),
        "universe_matched": written,
        "was_already_current": already_current,
    }
