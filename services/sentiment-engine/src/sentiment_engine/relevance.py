"""Relevance, novelty, source-quality heuristics -- EBIE EB-7
(increment 2). All pure functions, all disclosed v1 heuristics where
no better signal exists in Upstox's News API response (it carries no
per-article relevance/quality metadata of its own).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Real, observed publisher domains from Upstox's News API during EB-7
# increment 1's live verification, plus other well-known Indian
# financial-news domains -- a static credibility tier, not derived from
# any traffic/authority data source. Unknown domains default to 0.5
# (moderate, not penalized for being unrecognized).
_SOURCE_QUALITY_TIERS: dict[str, float] = {
    # Tier 1 -- major wire services / regulator-adjacent primary sources
    "reuters.com": 0.95,
    "bseindia.com": 0.95,
    "nseindia.com": 0.95,
    "pib.gov.in": 0.95,
    # Tier 2 -- established financial-news desks
    "economictimes.indiatimes.com": 0.8,
    "moneycontrol.com": 0.8,
    "livemint.com": 0.8,
    "business-standard.com": 0.8,
    "financialexpress.com": 0.75,
    "cnbctv18.com": 0.8,
    "bloombergquint.com": 0.8,
    "ndtvprofit.com": 0.75,
    # Tier 3 -- Upstox's own aggregated/syndicated market-wrap content
    "upstox.com": 0.65,
}
DEFAULT_SOURCE_QUALITY = 0.5

_WORD_RE = re.compile(r"[a-z0-9]+")


def parse_domain(article_link: str | None) -> str:
    if not article_link:
        return ""
    try:
        netloc = urlparse(article_link).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def source_quality(article_link: str | None) -> float:
    domain = parse_domain(article_link)
    return _SOURCE_QUALITY_TIERS.get(domain, DEFAULT_SOURCE_QUALITY)


def compute_relevance(symbol: str, heading: str, summary: str | None) -> float:
    """How directly this article concerns `symbol`, vs. being generic
    market commentary that happened to get the instrument_key tagged
    onto it (a real, observed pattern from increment 1's live
    verification -- e.g. a "NIFTY falls" market-wrap mentioning several
    index heavyweights in passing). Heuristic v1: does the symbol
    itself appear in the heading (strong signal -- the article is
    *about* this name) vs. only in the summary (weaker) vs. neither
    (the article is likely broad market coverage, only instrument-key-
    tagged, not really about this company)."""
    symbol_lower = symbol.lower()
    if symbol_lower in heading.lower():
        return 1.0
    if summary and symbol_lower in summary.lower():
        return 0.6
    return 0.3


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def compute_novelty(heading: str, recent_headlines: list[str]) -> float:
    """1.0 = genuinely new story; low = looks like a near-duplicate/
    re-syndication of something already seen recently for this symbol.
    Token-Jaccard similarity against each recent headline (already
    scoped to the same symbol and a recent window by the caller) --
    the closest real signal available without a full-text near-dup
    embedding model, which is out of scope for this increment."""
    if not recent_headlines:
        return 1.0
    current = _tokenize(heading)
    if not current:
        return 1.0
    best_overlap = 0.0
    for prior in recent_headlines:
        prior_tokens = _tokenize(prior)
        if not prior_tokens:
            continue
        union = current | prior_tokens
        if not union:
            continue
        jaccard = len(current & prior_tokens) / len(union)
        best_overlap = max(best_overlap, jaccard)
    if best_overlap >= 0.8:
        return 0.2
    if best_overlap >= 0.5:
        return 0.6
    return 1.0
