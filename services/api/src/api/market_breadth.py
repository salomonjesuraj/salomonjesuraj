"""Market breadth health score -- from ajeeshworkspace/indian-trading-skills's
5-component regime classifier, the last unbuilt candidate from the 11-link
GitHub research batch, built now on explicit request.

Real correction to the original research note before writing any code:
the plan flagged this as "requiring a new data feed... real cost, not a
drop-in," reasoning that Infusion's tick pipeline watches specific F&O
instruments, not the full NSE universe. That's true as far as it goes,
but re-checked against the CURRENT codebase (much of which didn't exist
when that note was written): every ingredient a 5-component breadth score
needs is already computed and cached, live, for the real 208-symbol F&O
universe --
  - change_pct / rsi_14 / rel_vol_20d: feature-engine's per-tick hot-state
    hash (infusion:feature:{symbol}), already used by scanner/sector.py's
    own per-sector breadth.
  - sma50 / sma200 / week52 near-high / near-low / Donchian fresh breakout:
    api/routes/mtf.py's daily-bar cache (infusion:mtf:{symbol}), built for
    Phase 13.5/13.12.
No new data feed. What's new here is aggregating these across the WHOLE
tracked universe into one composite read, not per-sector or per-symbol.

Scope, stated plainly: this measures breadth across Infusion's own
~208-symbol F&O-focused universe, NOT the full ~2,000-symbol NSE market.
"Market breadth" in the literature usually means the latter; this is
honestly a narrower, F&O-liquid-stock breadth proxy. Real, live, useful --
just not what "market breadth" means in a textbook sense, and labeled
that way everywhere it's surfaced.

Five components, each 0-100, equal-weighted into one composite (both the
equal weighting and the 0-100 component formulas are Infusion's own
calibration -- the source specifies "5 components" but not their exact
formulas or weights):

  1. Advance/decline breadth   -- % of symbols with change_pct > 0.
  2. Momentum breadth          -- % of symbols with rsi_14 > 50.
  3. Volume-weighted breadth   -- advancing symbols' summed rel_vol_20d as
     a share of (advancing + declining) summed rel_vol_20d -- an advance
     on heavy relative volume counts for more than one on a quiet day.
  4. Moving-average breadth    -- % of symbols (with a valid mtf cache)
     trading above BOTH their 50-day and 200-day SMA.
  5. 52-week-range breadth     -- (count near a 52w high - count near a
     52w low) / covered symbols, rescaled to 0-100.

Components 1-3 read the live feature hash (full ~208-symbol coverage,
every tick). Components 4-5 read the daily-bar mtf cache, which the
mtf_queue warming loop only keeps warm for a rolling subset at any given
moment (confirmed live: 42/208 symbols warm at a random check this
session, not all 208 continuously) -- each is gated on a minimum
covered-symbol count and reports its own real coverage rather than
silently averaging over whatever happened to be cached, matching this
session's standing "report real n, don't fabricate over gaps" discipline
(same shape as Feature-IC's n_present/n_absent gate, VCP's per-component
availability flags).

Informational only -- a situational-awareness read for the dashboard, not
wired into scanner suppression, scoring, or position sizing.
"""

from __future__ import annotations

import json

import msgpack

MIN_MTF_COVERAGE = 20  # a daily-bar-cache component needs at least this many covered symbols to count

# health_score -> regime label. Infusion's own calibration.
def _grade(score: float) -> str:
    if score >= 65.0:
        return "healthy"
    if score >= 45.0:
        return "neutral"
    return "weak"


async def _load_universe(redis) -> list[str]:
    raw = await redis.hgetall("infusion:symbols")
    symbols: list[str] = []
    for _, meta_raw in raw.items():
        try:
            meta = msgpack.unpackb(meta_raw, raw=False) if isinstance(meta_raw, bytes) else meta_raw
            sym = str(meta.get("symbol") or "").upper()
            if sym and meta.get("segment") != "INDEX":
                symbols.append(sym)
        except Exception:
            continue
    return symbols


def _decode_feature_hash(data: dict) -> dict:
    out = {}
    for k, v in (data or {}).items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        try:
            out[key] = float(val)
        except (TypeError, ValueError):
            out[key] = val
    return out


def _decode_mtf(raw) -> dict | None:
    if not raw:
        return None
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def build_breadth_result(
    *, n_total: int, n_live: int,
    advancing: int, declining: int, adv_vol: float, dec_vol: float,
    rsi_bullish: int, rsi_covered: int,
    ma_covered: int, ma_above_both: int,
    week52_covered: int, week52_near_high: int, week52_near_low: int,
) -> dict:
    """Pure aggregation -> result dict. Split out from compute_market_breadth()
    (which does the actual Redis pipeline read) specifically so the scoring
    math is unit-testable without a live Redis connection.
    """
    decided_ad = advancing + declining
    advance_decline_pct = round((advancing / decided_ad) * 100, 1) if decided_ad else None

    momentum_pct = round((rsi_bullish / rsi_covered) * 100, 1) if rsi_covered else None

    total_vol = adv_vol + dec_vol
    volume_breadth_pct = round((adv_vol / total_vol) * 100, 1) if total_vol > 0 else None

    ma_breadth = None
    if ma_covered >= MIN_MTF_COVERAGE:
        ma_breadth = round((ma_above_both / ma_covered) * 100, 1)

    week52_breadth = None
    if week52_covered >= MIN_MTF_COVERAGE:
        week52_breadth = round(50.0 + (week52_near_high - week52_near_low) / week52_covered * 50.0, 1)

    components = {
        "advance_decline": {
            "available": advance_decline_pct is not None, "score": advance_decline_pct,
            "advancing": advancing, "declining": declining, "n_covered": decided_ad,
        },
        "momentum": {
            "available": momentum_pct is not None, "score": momentum_pct,
            "rsi_bullish": rsi_bullish, "n_covered": rsi_covered,
        },
        "volume_weighted": {
            "available": volume_breadth_pct is not None, "score": volume_breadth_pct,
            "advancing_rel_vol": round(adv_vol, 2), "declining_rel_vol": round(dec_vol, 2),
        },
        "moving_average": {
            "available": ma_breadth is not None, "score": ma_breadth,
            "n_covered": ma_covered, "min_required": MIN_MTF_COVERAGE,
            "reason": None if ma_breadth is not None else f"Only {ma_covered} symbols have a warm daily-bar cache (need {MIN_MTF_COVERAGE}).",
        },
        "week52_range": {
            "available": week52_breadth is not None, "score": week52_breadth,
            "near_high": week52_near_high, "near_low": week52_near_low, "n_covered": week52_covered,
            "min_required": MIN_MTF_COVERAGE,
            "reason": None if week52_breadth is not None else f"Only {week52_covered} symbols have a warm daily-bar cache (need {MIN_MTF_COVERAGE}).",
        },
    }

    active_scores = [c["score"] for c in components.values() if c["available"]]
    health_score = round(sum(active_scores) / len(active_scores), 1) if active_scores else None

    return {
        "available": True,
        "scope": "Infusion's tracked F&O universe (~208 symbols), not the full NSE market -- see module docstring.",
        "universe_size": n_total,
        "n_live": n_live,
        "health_score": health_score,
        "regime": _grade(health_score) if health_score is not None else "unknown",
        "components": components,
        "n_active_components": len(active_scores),
    }


async def compute_market_breadth(redis) -> dict:
    if not redis:
        return {"available": False, "reason": "Redis not available."}

    symbols = await _load_universe(redis)
    if not symbols:
        return {"available": False, "reason": "No tracked F&O universe symbols found."}

    pipe = redis.pipeline(transaction=False)
    for sym in symbols:
        pipe.hgetall(f"infusion:feature:{sym}")
        pipe.get(f"infusion:mtf:{sym}")
    results = await pipe.execute()

    n_live = 0
    advancing = 0
    declining = 0
    rsi_bullish = 0
    rsi_covered = 0
    adv_vol = 0.0
    dec_vol = 0.0

    ma_covered = 0
    ma_above_both = 0
    week52_covered = 0
    week52_near_high = 0
    week52_near_low = 0

    for i, sym in enumerate(symbols):
        feat = _decode_feature_hash(results[i * 2])
        mtf = _decode_mtf(results[i * 2 + 1])

        ltp = feat.get("ltp")
        change_pct = feat.get("change_pct")
        rsi = feat.get("rsi_14")
        rel_vol = feat.get("rel_vol_20d")

        if feat:
            n_live += 1
        if isinstance(change_pct, float):
            if change_pct > 0:
                advancing += 1
                if isinstance(rel_vol, float):
                    adv_vol += rel_vol
            elif change_pct < 0:
                declining += 1
                if isinstance(rel_vol, float):
                    dec_vol += rel_vol
        if isinstance(rsi, float):
            rsi_covered += 1
            if rsi > 50:
                rsi_bullish += 1

        if mtf and isinstance(ltp, float) and ltp > 0:
            ma_regime = mtf.get("ma_regime") or {}
            sma50 = ma_regime.get("sma50")
            sma200 = ma_regime.get("sma200")
            if isinstance(sma50, (int, float)) and isinstance(sma200, (int, float)):
                ma_covered += 1
                if ltp > sma50 and ltp > sma200:
                    ma_above_both += 1

            week52 = mtf.get("week52") or {}
            if week52.get("week52_bars"):
                week52_covered += 1
                if week52.get("week52_near_high"):
                    week52_near_high += 1
                if week52.get("week52_near_low"):
                    week52_near_low += 1

    return build_breadth_result(
        n_total=len(symbols), n_live=n_live,
        advancing=advancing, declining=declining, adv_vol=adv_vol, dec_vol=dec_vol,
        rsi_bullish=rsi_bullish, rsi_covered=rsi_covered,
        ma_covered=ma_covered, ma_above_both=ma_above_both,
        week52_covered=week52_covered, week52_near_high=week52_near_high, week52_near_low=week52_near_low,
    )
