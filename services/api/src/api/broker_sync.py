"""Read-only Upstox broker sync -- "Broker Sync & Active Position
Intelligence" master sprint (2026-08-27).

STRICT ARCHITECTURAL RULE, honored end to end: every function here
reads from Upstox's real REST API; nothing in this module (or
routes/broker.py's thin wrapper) ever places, modifies, or cancels an
order. Trade execution stays 100% manual, on the broker's own platform
-- this module exists purely to observe and analyze the user's own
real account state, never to act on it. There is no POST route
anywhere in this file's call graph.

Verification disclosure: the Upstox v2 portfolio/order endpoint field
names below are implemented from Upstox's own documented API (the same
UPSTOX_API_V2_BASE / Bearer-token / error-envelope pattern
api/routes/market.py already uses for its own real option-chain calls),
but this session had no live account with open positions, holdings, or
pending orders to confirm every field name against a populated real
response. Every read below is defensive (.get() with a safe default,
never an assumed-present key) -- a field this code expected but Upstox
didn't send just comes back None/0, not a crash. Worth a real check the
first time a trader has an actual open position while this runs.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp
from infusion_models.smc import nearest_ob_or_fvg_level, structural_invalidation

Payload = dict[str, Any]

UPSTOX_API_V2_BASE = "https://api.upstox.com/v2"

# ── Duplicated from api/routes/market.py's own identical functions --
# same "2 services duplicating is fine, a 3rd consumer is what triggers
# a shared module" precedent infusion_models.smc's own docstring
# already established. This is consumer #2; not promoted yet. ──────────


async def _upstox_access_token(redis: Any) -> str:
    auth_raw = await redis.get("infusion:auth:upstox")
    if auth_raw:
        try:
            auth_text = auth_raw.decode() if isinstance(auth_raw, bytes) else auth_raw
            auth_payload = json.loads(auth_text)
            token = auth_payload.get("access_token", "") if isinstance(auth_payload, dict) else ""
            if token:
                return str(token)
        except Exception:
            pass
    return os.getenv("INFUSION_UPSTOX_ACCESS_TOKEN", "")


def _upstox_error_reason(payload: Payload, status: int) -> str:
    if status == 429:
        return "Upstox rate limit hit (429/UDAPI10005) -- back off before retrying."
    if status == 401:
        return "Upstox token invalid or expired (401) -- login again at http://localhost:5100/auth/login."
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = first.get("errorCode", "")
        msg = first.get("message", "")
        if code or msg:
            return f"{code}: {msg}".strip(": ") or f"Upstox error HTTP {status}"
    flat_msg = payload.get("message") if isinstance(payload, dict) else None
    if flat_msg:
        return str(flat_msg)
    return f"Upstox error HTTP {status}"


def _num(row: Payload, key: str, default: float = 0.0) -> float:
    try:
        val = row.get(key)
        return float(val) if val not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _opt_f(row: Payload, key: str) -> float | None:
    val = row.get(key)
    if val in (None, "", "None"):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


async def _headers(redis: Any) -> tuple[dict[str, str], str]:
    """Returns (headers, error) -- error is non-empty when there's no
    token to build real headers from, in which case headers is {}."""
    access_token = await _upstox_access_token(redis)
    if not access_token:
        return {}, "Upstox auth token missing; login at http://localhost:5100/auth/login."
    return {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}, ""


async def _get_upstox(redis: Any, path: str) -> Payload:
    """One real GET against Upstox's v2 API, honest failure envelope on
    any problem (missing token, network error, non-success response).
    Every caller in this module goes through this single function --
    there is no POST/PUT/DELETE call anywhere in this module."""
    headers, error = await _headers(redis)
    if error:
        return {"available": False, "reason": error}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"{UPSTOX_API_V2_BASE}{path}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp,
        ):
            data = await resp.json()
            if resp.status != 200 or data.get("status") != "success":
                return {"available": False, "reason": _upstox_error_reason(data, resp.status)}
            return {"available": True, "data": data.get("data") or []}
    except Exception as exc:
        return {"available": False, "reason": f"Upstox request to {path} failed: {exc}"}


# ── Read-only fetches -- Phase 1 ────────────────────────────────────────


async def fetch_positions(redis: Any) -> Payload:
    """GET /portfolio/short-term-positions -- today's intraday +
    overnight-carry F&O/equity positions, real Upstox M2M pnl per row."""
    result = await _get_upstox(redis, "/portfolio/short-term-positions")
    if not result.get("available"):
        return result
    rows = [r for r in result["data"] if isinstance(r, dict)]
    return {"available": True, "count": len(rows), "positions": rows}


async def fetch_holdings(redis: Any) -> Payload:
    """GET /portfolio/long-term-holdings -- delivery equity holdings."""
    result = await _get_upstox(redis, "/portfolio/long-term-holdings")
    if not result.get("available"):
        return result
    rows = [r for r in result["data"] if isinstance(r, dict)]
    return {"available": True, "count": len(rows), "holdings": rows}


async def fetch_orders(redis: Any) -> Payload:
    """GET /order/retrieve-all -- today's real order book, every status
    Upstox reports passed through as-is (its own real strings, e.g.
    "open"/"complete"/"cancelled"/"rejected"/"trigger pending" -- not
    remapped into a taxonomy this codebase invented)."""
    result = await _get_upstox(redis, "/order/retrieve-all")
    if not result.get("available"):
        return result
    rows = [r for r in result["data"] if isinstance(r, dict)]
    return {"available": True, "count": len(rows), "orders": rows}


# ── Phase 2: Position Decision & Horizon Engine ─────────────────────────
# Reuses the exact same real structural geometry api/trade_blueprint.py
# already reuses (compute_mtf's fractal-pivot "Major Blocker" + real
# Donchian channel) and the same shared Fast Exit rule
# (infusion_models.smc.structural_invalidation) -- no new geometry
# engine built for this sprint, only new position-level synthesis on
# top of it.

THETA_RISK_SEVERE_DTE = 1
THETA_RISK_ACCELERATING_DTE = 5

_MONTH_ABBR = {
    m: i + 1
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    )
}
# Confirmed against two real live positions the first time this endpoint
# ever ran against a real account (POWERGRID26SEP280CE, KAYNES26SEP4200CE)
# -- Upstox's own real compact F&O trading-symbol format is
# <SYMBOL><DD><MON><STRIKE><CE|PE> (options) / <SYMBOL><DD><MON>FUT
# (futures), with NO year encoded at all. An earlier version of this
# parser assumed a "DD MON YY" convention with a trailing year group and
# mistook the real strike price for a year on both of these live rows,
# producing a nonsense multi-century DTE -- caught immediately because
# the live number was obviously wrong (567,191 "trading days"), not a
# quiet miscalculation. Fixed to match what Upstox actually sends and to
# infer the missing year from "nearest occurrence at/after today" (see
# _infer_expiry_year) rather than assume a format that isn't real.
_OPTION_TAIL_RE = re.compile(r"(\d{1,2})([A-Z]{3})\d+(?:\.\d+)?(?:CE|PE)$")
_FUTURES_TAIL_RE = re.compile(r"(\d{1,2})([A-Z]{3})FUT$")


def _infer_expiry_year(day: int, month: int, today: date) -> date | None:
    """NSE never lists an F&O contract more than roughly a year out, so
    the nearest occurrence of this day+month at or after today (minus a
    small grace window for a contract that expired only a few days ago
    but is still returned in today's position snapshot) is the correct
    year to pair with a year-less compact symbol -- not a guess standing
    in for a real value, a real constraint of how NSE F&O listings work.
    """
    grace = today - timedelta(days=3)
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= grace:
            return candidate
    try:
        return date(today.year + 1, month, day)
    except ValueError:
        return None


def underlying_symbol(trading_symbol: str) -> str:
    """Best-effort extraction of the underlying equity/index symbol from
    an F&O trading symbol -- the leading alphabetic run before the
    expiry/strike digits start (e.g. "RELIANCE" from
    "RELIANCE28DEC2900CE", "NIFTY" from "NIFTY28DEC24500PE"). A plain
    equity symbol (no digits at all) returns unchanged.

    This is a heuristic over Upstox's own real trading_symbol text, not
    a lookup against an authoritative instrument master this pipeline
    doesn't have cached locally. If it ever misparses a real symbol,
    every structural field downstream of it just comes back unavailable
    for that one row -- never a wrong-but-confident guess standing in
    for the real underlying.
    """
    cleaned = trading_symbol.strip().upper()
    match = re.match(r"^([A-Z&\-]+)\d", cleaned)
    return match.group(1) if match else cleaned


def extract_expiry(row: Payload, today: date | None = None) -> date | None:
    """Real expiry date for an F&O position, if it can be honestly
    determined. Tries, in order: (1) a literal `expiry` field some
    Upstox response variants include directly, (2) the day+month
    embedded in Upstox's own real compact trading-symbol format
    (<SYMBOL><DD><MON><STRIKE><CE|PE> / <SYMBOL><DD><MON>FUT -- see
    _OPTION_TAIL_RE's own comment for how this was confirmed live, and
    why there's no year to parse, only to infer). Equity positions (no
    expiry at all) and anything neither path resolves return None --
    never a fabricated date standing in for a real one that isn't
    there. `today` is injectable for tests; defaults to the real date.
    """
    today = today or date.today()
    raw_expiry = row.get("expiry")
    if raw_expiry:
        text = str(raw_expiry)
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue

    symbol = str(row.get("trading_symbol") or row.get("tradingsymbol") or "").upper()
    match = _OPTION_TAIL_RE.search(symbol) or _FUTURES_TAIL_RE.search(symbol)
    if not match:
        return None
    day_text, month_text = match.groups()
    month = _MONTH_ABBR.get(month_text)
    if month is None:
        return None
    return _infer_expiry_year(int(day_text), month, today)


def compute_dte(expiry: date | None, today: date) -> tuple[int | None, str]:
    """Trading days (Mon-Fri only; no NSE holiday calendar is consulted
    here, disclosed rather than silently assumed accurate to the day)
    to expiry, plus Infusion's own theta-decay-risk calibration -- same
    "not from a cited source" posture as this codebase's other
    thresholds (retest.py's RETEST_BAND_ATR, futures.py's
    OI_BUILDUP_DEADBAND_PCT, etc.). (None, "N/A") for a position with no
    real expiry (an equity position), never a fabricated DTE.
    """
    if expiry is None:
        return None, "N/A"
    if expiry <= today:
        return 0, "SEVERE"
    trading_days = 0
    cursor = today
    while cursor < expiry:
        cursor = cursor + timedelta(days=1)
        if cursor.weekday() < 5:
            trading_days += 1
    if trading_days <= THETA_RISK_SEVERE_DTE:
        risk = "SEVERE"
    elif trading_days <= THETA_RISK_ACCELERATING_DTE:
        risk = "ACCELERATING"
    else:
        risk = "LOW"
    return trading_days, risk


# Upstox's real `product` codes: "I" (intraday/MIS), "D" (delivery/CNC),
# "CO" (cover order), "OCO" (one-cancels-other bracket). Only "I" carries
# no overnight-carry -- everything else CAN be held past today.
INTRADAY_PRODUCT_CODES = {"I", "MIS"}


def classify_holding_horizon(
    *,
    invalidation_tags: list[str],
    theta_risk: str,
    dte_trading_days: int | None,
    product: str,
    trend_aligned: bool,
) -> str:
    """Deterministic synthesis of DTE/theta risk, real structural
    alignment, and product type into one decision tag -- Infusion's own
    calibration (not a cited rule), checked most-urgent-first so a real
    invalidation always wins over a merely-tight DTE, which always wins
    over a plain intraday-product default."""
    if invalidation_tags:
        return "EXIT IMMEDIATELY"
    if theta_risk == "SEVERE":
        return "TIGHTEN STOP"
    if product.upper() in INTRADAY_PRODUCT_CODES:
        return "RUNNER (INTRADAY ONLY)" if trend_aligned else "TIGHTEN STOP"
    if trend_aligned and theta_risk in {"LOW", "ACCELERATING"}:
        return "HOLD (2-3 DAYS)"
    return "TIGHTEN STOP"


async def _feature_row(redis: Any, symbol: str) -> Payload:
    raw = await redis.hgetall(f"infusion:feature:{symbol}")
    out: Payload = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        out[key] = v.decode() if isinstance(v, bytes) else v
    return out


async def compute_position_intelligence(redis: Any, position: Payload) -> Payload:
    """Everything Phase 2 asked for, per real open position: DTE +
    theta risk, dynamic turning points (real HTF support/resistance +
    Donchian channel + nearest OB/FVG level), the shared Reversal &
    Invalidation Watch, and the synthesized holding-horizon tag. Every
    field is None/honestly-labeled when its own real upstream source
    has nothing for this position -- see this module's own top-level
    docstring for the field-name verification disclosure.
    """
    trading_symbol = str(position.get("trading_symbol") or position.get("tradingsymbol") or "")
    underlying = underlying_symbol(trading_symbol)

    quantity = _num(position, "quantity")
    day_buy = _num(position, "day_buy_quantity")
    day_sell = _num(position, "day_sell_quantity")
    bullish = quantity > 0 if quantity != 0 else day_buy >= day_sell

    ltp = _num(position, "last_price")
    expiry = extract_expiry(position)
    dte_trading_days, theta_risk = compute_dte(expiry, date.today())

    from api.routes.mtf import compute_mtf

    try:
        mtf = await compute_mtf(redis, underlying, store=False)
    except Exception:
        mtf = {}
    donchian = mtf.get("donchian") or {}
    support = _opt_f(mtf, "blocker_down_level")
    resistance = _opt_f(mtf, "blocker_up_level")
    channel_lower = _opt_f(donchian, "low")
    channel_upper = _opt_f(donchian, "high")

    feature_row = await _feature_row(redis, underlying)
    trend_text = str(feature_row.get("trend_text") or "RANGE / UNDEFINED")
    trend_aligned = ("UPTREND" in trend_text and bullish) or (
        "DOWNTREND" in trend_text and not bullish
    )
    ob_fvg_level = nearest_ob_or_fvg_level(feature_row, bearish=not bullish)

    invalidation_tags = (
        structural_invalidation(
            bullish=bullish,
            ltp=ltp,
            support=support,
            resistance=resistance,
            channel_lower=channel_lower,
            channel_upper=channel_upper,
        )
        if ltp > 0
        else []
    )

    product = str(position.get("product") or "")
    horizon = classify_holding_horizon(
        invalidation_tags=invalidation_tags,
        theta_risk=theta_risk,
        dte_trading_days=dte_trading_days,
        product=product,
        trend_aligned=trend_aligned,
    )

    return {
        "underlying": underlying,
        "direction": "BULL" if bullish else "BEAR",
        "dte_trading_days": dte_trading_days,
        "theta_risk": theta_risk,
        "expiry": expiry.isoformat() if expiry else None,
        "structure": {
            "support": support,
            "resistance": resistance,
            "channel_upper": channel_upper,
            "channel_lower": channel_lower,
            "trend": trend_text,
        },
        # "How Far Can It Go" / "Where Will It Turn" -- primary target is
        # the nearer real HTF level in this position's favor, secondary
        # extension is the wider Donchian bound; invalidation is the
        # nearer real level AGAINST this position (the same level
        # structural_invalidation's FAST_EXIT check watches).
        "target_primary": resistance if bullish else support,
        "target_secondary": channel_upper if bullish else channel_lower,
        "invalidation_level": support if bullish else resistance,
        "nearest_ob_fvg_level": ob_fvg_level,
        "trend_aligned": trend_aligned,
        "warning_tags": invalidation_tags,
        "holding_horizon": horizon,
    }


async def fetch_positions_with_intelligence(redis: Any) -> Payload:
    """The real GET /api/broker/positions payload: Upstox's own live
    rows, each carrying an `intelligence` object built by
    compute_position_intelligence above, plus a real portfolio-level
    aggregate strip. Rows with quantity == 0 are today's fully-closed-
    out positions Upstox still lists for the day -- filtered out here
    since they're history, not an active position to analyze.
    """
    base = await fetch_positions(redis)
    if not base.get("available"):
        return base

    active = [p for p in base["positions"] if _num(p, "quantity") != 0]
    enriched: list[Payload] = []
    for position in active:
        intelligence = await compute_position_intelligence(redis, position)
        enriched.append({**position, "intelligence": intelligence})

    total_unrealized = sum(_num(p, "pnl") for p in active)
    total_realized = sum(_num(p, "realised") for p in active)
    capital_deployed = sum(
        abs(_num(p, "quantity")) * _num(p, "average_price") * _num(p, "multiplier", 1.0)
        for p in active
    )
    # "Total Open Risk" has no real per-position stop to sum -- these
    # are the trader's own manually-placed broker positions, not this
    # system's own paper trades with a planned SL already on file. The
    # honest number available is distance-to-the-real-computed-
    # invalidation-level per position, summed only over rows where that
    # level is actually known (never padded with a guess for the rest).
    structural_risk_total = 0.0
    structural_risk_known_for = 0
    for row in enriched:
        level = row["intelligence"].get("invalidation_level")
        if level is None:
            continue
        structural_risk_total += abs(_num(row, "quantity")) * abs(_num(row, "last_price") - level)
        structural_risk_known_for += 1

    return {
        "available": True,
        "count": len(enriched),
        "positions": enriched,
        "portfolio": {
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_realized_pnl": round(total_realized, 2),
            "capital_deployed": round(capital_deployed, 2),
            "structural_risk_estimate": round(structural_risk_total, 2),
            "structural_risk_known_for": structural_risk_known_for,
            "structural_risk_total_positions": len(enriched),
        },
    }
