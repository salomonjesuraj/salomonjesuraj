"""EBIE EB-7 (increment 3) -- live sentiment_impact compositing.

Per migration 009's own docstring: age_decay is deliberately NOT baked
into sentiment_scores at classification time (decay is a function of
elapsed time since publish, not a static article property). This
module computes it live, at read time, from the decay-independent
components sentiment-engine persisted (direction, confidence, severity,
relevance, novelty, source_quality) -- pure functions, no I/O, verified
in isolation the same way every other EBIE compositing module has been
this session.

Calibration disclosure, matching the rest of EB-7's honesty convention:
DECAY_HALF_LIFE_HOURS and the BULLISH/BEARISH labeling threshold below
are v1 choices, not yet validated against real outcome data (there
isn't enough classified+decided history yet to calibrate against).
Revisit once enough real signals with attached news history exist to
check whether these thresholds actually track real forward moves.
"""

from __future__ import annotations

from typing import Any

DECAY_HALF_LIFE_HOURS = 24.0

# bullish=+1 / bearish=-1 -- direction is a genuine directional edge.
# neutral=0 (FinBERT itself found no lean) and unknown=0 (sentiment-
# engine's own model-unavailable fallback, per Q4.2's authorized
# UNKNOWN failure mode) both carry zero directional sign AND, because
# summarize_symbol_sentiment weights by |impact|, zero weight in the
# aggregate -- an UNKNOWN article is excluded from influencing the
# average, not silently counted as a "neutral" data point that would
# drag it toward zero.
DIRECTION_SIGN: dict[str, float] = {
    "bullish": 1.0,
    "bearish": -1.0,
    "neutral": 0.0,
    "ambiguous": 0.0,
    "unknown": 0.0,
}

# Below this |weighted_impact|, report NEUTRAL rather than a directional
# label -- real observed data (EB-7 increment 2's live verification)
# shows most articles land as event_type='other' (severity 0.2), which
# alone caps realistic impact magnitudes well under 1.0, so this is
# deliberately a small threshold, not 0.5.
LABEL_THRESHOLD = 0.05

RECENCY_WINDOW_MS = 7 * 24 * 3600 * 1000  # matches Upstox News API's own 7-day recency window


def compute_age_decay(published_time_ms: int | None, now_ms: int) -> float:
    """Exponential decay, half-life DECAY_HALF_LIFE_HOURS. An article
    with no known publish time decays to 0 (maximally stale) rather
    than being fabricated as fresh."""
    if not published_time_ms:
        return 0.0
    age_hours = max(0.0, (now_ms - published_time_ms) / 3_600_000.0)
    return float(0.5 ** (age_hours / DECAY_HALF_LIFE_HOURS))


def compute_sentiment_impact(
    direction: str,
    confidence: float,
    severity: float,
    relevance: float,
    novelty: float,
    source_quality: float,
    age_decay: float,
) -> float:
    """docs/EBIE-BLUEPRINT.md Section 4.10's own formula: direction *
    confidence * event_severity * stock_relevance * novelty *
    source_quality * age_decay. An unrecognized direction string
    degrades to sign 0 (no directional influence) rather than raising."""
    sign = DIRECTION_SIGN.get(direction, 0.0)
    return sign * confidence * severity * relevance * novelty * source_quality * age_decay


def summarize_symbol_sentiment(rows: list[dict[str, Any]], now_ms: int) -> dict[str, Any]:
    """rows: each a dict with direction/confidence/severity/relevance/
    novelty/source_quality/published_time_ms/heading/event_type (the
    shape a news_events JOIN sentiment_scores query naturally produces).
    Weighted-average impact, weighted by |impact| itself -- a real,
    high-conviction, still-fresh article should dominate the aggregate
    far more than a stale/barely-relevant/unknown-direction one, which
    correctly contributes near-zero weight rather than diluting the
    average as if it were a genuine neutral read."""
    if not rows:
        return {"available": False, "reason": "No recent news for this symbol.", "article_count": 0}

    scored: list[dict[str, Any]] = []
    for r in rows:
        decay = compute_age_decay(r.get("published_time_ms"), now_ms)
        impact = compute_sentiment_impact(
            r.get("direction", "unknown"),
            float(r.get("confidence") or 0.0),
            float(r.get("severity") or 0.0),
            float(r.get("relevance") or 0.0),
            float(r.get("novelty") or 0.0),
            float(r.get("source_quality") or 0.0),
            decay,
        )
        scored.append({**r, "age_decay": round(decay, 4), "impact": round(impact, 4)})

    weights = [abs(s["impact"]) for s in scored]
    total_weight = sum(weights)
    weighted_impact = (
        sum(s["impact"] * w for s, w in zip(scored, weights, strict=False)) / total_weight
        if total_weight > 0
        else 0.0
    )

    if weighted_impact >= LABEL_THRESHOLD:
        label = "BULLISH"
    elif weighted_impact <= -LABEL_THRESHOLD:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    most_impactful = max(scored, key=lambda s: abs(s["impact"]))

    return {
        "available": True,
        "article_count": len(scored),
        "weighted_impact": round(weighted_impact, 4),
        "sentiment": label,
        "most_impactful_article": {
            "heading": most_impactful.get("heading"),
            "event_type": most_impactful.get("event_type"),
            "direction": most_impactful.get("direction"),
            "impact": most_impactful.get("impact"),
            "published_time_ms": most_impactful.get("published_time_ms"),
        },
    }
