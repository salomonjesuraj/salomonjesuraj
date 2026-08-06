"""Free starter news feed for market context.

Phase 1 uses GDELT because it is free and near-real-time enough for a dashboard
context panel.  It is not a low-latency trading newswire and must not be treated
as confirmation by itself.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from aiohttp import web

routes = web.RouteTableDef()
NEWS_EDGE_KEY_PREFIX = "infusion:news-edge:"
IST = timezone(timedelta(hours=5, minutes=30))

POSITIVE_WORDS = {
    "profit", "profits", "beats", "beat", "record", "upgrade", "upgraded",
    "order win", "wins order", "approval", "approved", "launch", "expansion",
    "rally", "surge", "gains", "growth", "strong", "bullish", "buy",
    "outperform", "raises", "hike", "dividend", "bonus", "split",
}
NEGATIVE_WORDS = {
    "loss", "losses", "misses", "downgrade", "downgraded", "probe", "fraud",
    "penalty", "fine", "raid", "fire", "accident", "recall", "fall", "falls",
    "slump", "weak", "bearish", "sell", "cuts", "cut", "lawsuit", "debt",
    "default", "resigns", "resignation",
}
EVENT_RISK_WORDS = {
    "results", "earnings", "board meeting", "merger", "acquisition", "stake sale",
    "block deal", "bulk deal", "pledge", "pledged", "rights issue", "qip",
    "f&o ban", "ban", "supreme court", "rbi", "sebi", "government", "tariff",
    "strike", "shutdown",
}


def _headline_score(title: str, tone) -> tuple[float, list[str], list[str]]:
    text = f" {str(title or '').lower()} "
    score = 0.0
    tags: list[str] = []
    risks: list[str] = []
    for word in POSITIVE_WORDS:
        if word in text:
            score += 1.0
            tags.append(word)
    for word in NEGATIVE_WORDS:
        if word in text:
            score -= 1.0
            tags.append(word)
    for word in EVENT_RISK_WORDS:
        if word in text:
            risks.append(word)
            score -= 0.25
    try:
        t = float(tone)
        if t > 1.5:
            score += 0.5
            tags.append("positive tone")
        elif t < -1.5:
            score -= 0.5
            tags.append("negative tone")
    except (TypeError, ValueError):
        pass
    return score, tags[:4], risks[:4]


def _news_edge(items: list[dict]) -> dict:
    if not items:
        return {
            "stance": "NO_NEWS",
            "score": 0,
            "confidence": "LOW",
            "action": "Do not use news as confirmation.",
            "blockers": ["No fresh public articles found"],
            "boosters": [],
            "risks": [],
        }

    scored = []
    risk_hits: list[str] = []
    boosters: list[str] = []
    blockers: list[str] = []
    total = 0.0
    for item in items[:8]:
        score, tags, risks = _headline_score(item.get("title", ""), item.get("tone"))
        item["headline_score"] = round(score, 2)
        item["tags"] = tags
        item["event_risks"] = risks
        scored.append(score)
        total += score
        if score > 0.5:
            boosters.append(item.get("title", "")[:110])
        elif score < -0.5:
            blockers.append(item.get("title", "")[:110])
        risk_hits.extend(risks)

    avg = total / max(len(scored), 1)
    unique_risks = list(dict.fromkeys(risk_hits))[:6]
    if unique_risks and abs(avg) < 1.25:
        stance = "EVENT_RISK"
    elif avg >= 0.65:
        stance = "BULLISH"
    elif avg <= -0.65:
        stance = "BEARISH"
    else:
        stance = "NEUTRAL"
    confidence = "HIGH" if len(items) >= 5 and abs(avg) >= 1.0 else "MEDIUM" if len(items) >= 3 else "LOW"
    if stance == "BULLISH":
        action = "News supports CE bias only if scanner and option contract also agree."
    elif stance == "BEARISH":
        action = "News warns against CE and may support PE only with price confirmation."
    elif stance == "EVENT_RISK":
        action = "Event risk present; reduce size or wait for price confirmation."
    else:
        action = "News is neutral; rely on scanner, MTF, and option-chain gates."

    return {
        "stance": stance,
        "score": round(avg, 2),
        "confidence": confidence,
        "action": action,
        "blockers": blockers[:3],
        "boosters": boosters[:3],
        "risks": unique_risks,
    }


def _compact_article(item: dict) -> dict:
    return {
        "title": item.get("title") or "",
        "url": item.get("url") or "",
        "source": item.get("domain") or item.get("sourcecountry") or item.get("sourceCountry") or "",
        "published": item.get("seendate") or "",
        "tone": item.get("tone"),
        "language": item.get("language") or "",
    }


async def _cache_news_edge(request, symbol: str, sector: str, source: str, edge: dict, items: list[dict]) -> None:
    if not symbol:
        return
    redis = request.app.get("redis")
    if not redis:
        return
    payload = {
        "symbol": symbol,
        "sector": sector,
        "source": source,
        "edge": edge,
        "items": items[:5],
        "cached_at_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
    }
    try:
        await redis.set(
            f"{NEWS_EDGE_KEY_PREFIX}{symbol}",
            json.dumps(payload, separators=(",", ":")),
            ex=60 * 45,
        )
    except Exception:
        pass


def _rss_items(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    out = []
    for item in root.findall(".//item")[:8]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        source = item.findtext("source") or "Google News"
        if title and link:
            out.append(
                {
                    "title": title,
                    "url": link,
                    "source": source,
                    "published": pub,
                    "tone": None,
                    "language": "English",
                }
            )
    return out


async def _google_news_fallback(session, query: str) -> list[dict]:
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=en-IN&gl=IN&ceid=IN:en"
    )
    try:
        async with session.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status != 200:
                return []
            return _rss_items(await resp.text())
    except Exception:
        return []


@routes.get("/api/news/market")
async def market_news(request):
    session = request.app.get("http_session")
    symbol = str(request.query.get("symbol") or "").upper().strip()
    sector = str(request.query.get("sector") or "").upper().strip()
    if not session:
        return web.json_response({"ok": False, "items": [], "note": "HTTP session unavailable"})

    query_parts = []
    if symbol:
        query_parts.append(f'"{symbol}"')
    if sector:
        query_parts.append(sector.replace("_", " "))
    query_parts.extend(["NSE", "stock", "India"])
    query = " ".join(query_parts)

    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?"
        f"query={quote_plus(query)}"
        "&mode=artlist"
        "&format=json"
        "&maxrecords=12"
        "&sort=hybridrel"
    )
    try:
        async with session.get(url, timeout=12) as resp:
            if resp.status != 200:
                fallback = await _google_news_fallback(session, query)
                if fallback:
                    edge = _news_edge(fallback)
                    await _cache_news_edge(request, symbol, sector, "Google News RSS fallback", edge, fallback)
                    return web.json_response(
                        {
                            "ok": True,
                            "symbol": symbol,
                            "sector": sector,
                            "source": "Google News RSS fallback",
                            "items": fallback,
                            "edge": edge,
                            "note": f"GDELT returned HTTP {resp.status}; showing free RSS fallback context.",
                        }
                    )
                return web.json_response(
                    {"ok": False, "items": [], "note": f"Free news source HTTP {resp.status}"}
                )
            data = await resp.json(content_type=None)
    except Exception as exc:
        fallback = await _google_news_fallback(session, query)
        if fallback:
            edge = _news_edge(fallback)
            await _cache_news_edge(request, symbol, sector, "Google News RSS fallback", edge, fallback)
            return web.json_response(
                {
                    "ok": True,
                    "symbol": symbol,
                    "sector": sector,
                    "source": "Google News RSS fallback",
                    "items": fallback,
                    "edge": edge,
                    "note": f"GDELT failed ({type(exc).__name__}); showing free RSS fallback context.",
                }
            )
        return web.json_response({"ok": False, "items": [], "note": f"Free news fetch failed: {type(exc).__name__}: {exc}"})

    articles = data.get("articles") if isinstance(data, dict) else []
    items = [_compact_article(x) for x in (articles or []) if x.get("title") and x.get("url")]
    edge = _news_edge(items)
    await _cache_news_edge(request, symbol, sector, "GDELT", edge, items)
    return web.json_response(
        {
            "ok": True,
            "symbol": symbol,
            "sector": sector,
            "source": "GDELT",
            "items": items[:8],
            "edge": edge,
            "note": "Free public-news context only; not a low-latency trading newswire.",
        }
    )
