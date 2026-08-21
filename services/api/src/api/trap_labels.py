"""EBIE EB-9 (increment 2) -- false-break outcome labeling from real
archived signal history.

Per docs/EBIE-BLUEPRINT.md Section 18: "Build a dedicated trap
probability model... Track trap outcome explicitly." This is the
ground-truth CAPTURE step, not the model itself -- deliberately scoped
down, matching this session's established "capture real labeled data
before training anything" discipline (the same shape as Phase 13.4's
option-premium capture, which explicitly deferred net-of-cost reporting
to 13.4b once real premium data existed to check it against).

The real, checkable proxy for "this setup crossed its trigger then
reversed" that this system can compute TODAY, from data it already
archives, without any new tracking infrastructure: a signal that
resolved via STOP_HIT very soon after firing. A slower STOP_HIT (the
setup traded correctly for a while, then genuinely failed later) is a
different, less trap-like failure mode -- disclosed as a real scope
boundary of this v1 label, not silently conflated with a fast reversal.
TARGET_HIT is, by construction, never a false break. PENDING/EXPIRED
signals have no clear resolution either way and are excluded, not
guessed at.

Once real trap_risk_score (EB-9 increment 1) history accumulates on
signals fired AFTER that field started being computed, this module also
checks whether the heuristic's own score actually correlates with real
false-break outcomes -- the honest, checkable question EB-9 exists to
answer, gated on a real minimum sample size (same governance as Feature-
IC's own min_side gate) rather than reported on a handful of rows.
"""

from __future__ import annotations

import json

FALSE_BREAK_FAST_STOP_MIN = (
    15.0  # a STOP_HIT within this many minutes of firing reads as a false break
)
MIN_SAMPLE_FOR_RATE = 20  # don't report a rate off a handful of decided signals
MIN_SAMPLE_PER_BUCKET = 15  # same shape as Feature-IC's min_side


def classify_false_break(outcome_label: str | None, time_to_stop_min: float | None) -> bool | None:
    """None (unavailable) unless the signal actually resolved via
    TARGET_HIT or STOP_HIT -- never fabricated for PENDING/EXPIRED
    rows, or for a STOP_HIT whose timing is missing."""
    if outcome_label == "TARGET_HIT":
        return False
    if outcome_label == "STOP_HIT":
        if time_to_stop_min is None:
            return None
        return float(time_to_stop_min) <= FALSE_BREAK_FAST_STOP_MIN
    return None


def _decode_json(raw) -> dict:
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def _trap_bucket(trap_risk_score: float | None) -> str | None:
    if trap_risk_score is None:
        return None
    if trap_risk_score < 33.4:
        return "low"
    if trap_risk_score < 66.7:
        return "mid"
    return "high"


def _rate(labels: list[bool]) -> float | None:
    return round(100 * sum(labels) / len(labels), 1) if labels else None


async def compute_false_break_stats(pool, days: int = 90) -> dict:
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available."}

    days = max(1, min(365, int(days or 90)))
    async with pool.acquire() as conn:
        try:
            records = await conn.fetch(
                """
                SELECT outcome_label, time_to_stop_min, strategy, conviction_grade, sub_scores
                FROM signals
                WHERE created_at >= now() - ($1::int * interval '1 day')
                  AND outcome_label IN ('TARGET_HIT', 'STOP_HIT')
                  AND NOT COALESCE(suppressed, false)
                """,
                days,
            )
        except Exception as exc:
            return {"available": False, "reason": f"false-break query failed: {exc}"}

    rows = []
    for r in records:
        d = dict(r)
        label = classify_false_break(d.get("outcome_label"), d.get("time_to_stop_min"))
        if label is None:
            continue
        sub_scores = _decode_json(d.get("sub_scores"))
        trap = (sub_scores.get("trap_risk") or {}).get("trap_risk_score")
        rows.append(
            {
                "false_break": label,
                "strategy_id": d.get("strategy"),
                "conviction_grade": d.get("conviction_grade"),
                "trap_risk_score": trap,
            }
        )

    total_labeled = len(rows)
    overall_rate = _rate([r["false_break"] for r in rows])

    by_strategy: dict[str, list[bool]] = {}
    by_grade: dict[str, list[bool]] = {}
    for r in rows:
        by_strategy.setdefault(r["strategy_id"] or "unknown", []).append(r["false_break"])
        by_grade.setdefault(r["conviction_grade"] or "unknown", []).append(r["false_break"])

    strategy_breakdown = [
        {
            "strategy_id": k,
            "n": len(v),
            "false_break_rate_pct": _rate(v),
            "reliable": len(v) >= MIN_SAMPLE_FOR_RATE,
        }
        for k, v in sorted(by_strategy.items(), key=lambda kv: -len(kv[1]))
    ]
    grade_breakdown = [
        {
            "conviction_grade": k,
            "n": len(v),
            "false_break_rate_pct": _rate(v),
            "reliable": len(v) >= MIN_SAMPLE_FOR_RATE,
        }
        for k, v in sorted(by_grade.items(), key=lambda kv: -len(kv[1]))
    ]

    # Does the EB-9 increment-1 heuristic's own trap_risk_score actually
    # track real false-break outcomes? Only signals fired AFTER that
    # field started being computed carry it -- reported honestly with
    # its real (likely small, at first) sample size, not padded or
    # skipped silently.
    by_bucket: dict[str, list[bool]] = {"low": [], "mid": [], "high": []}
    scored_n = 0
    for r in rows:
        bucket = _trap_bucket(r["trap_risk_score"])
        if bucket is None:
            continue
        scored_n += 1
        by_bucket[bucket].append(r["false_break"])

    bucket_breakdown = [
        {
            "trap_risk_bucket": k,
            "n": len(v),
            "false_break_rate_pct": _rate(v),
            "reliable": len(v) >= MIN_SAMPLE_PER_BUCKET,
        }
        for k, v in by_bucket.items()
    ]
    heuristic_reliable = (
        scored_n >= MIN_SAMPLE_FOR_RATE
        and all(len(v) >= MIN_SAMPLE_PER_BUCKET for v in by_bucket.values() if v)
        and sum(1 for v in by_bucket.values() if v) >= 2
    )

    return {
        "available": True,
        "days": days,
        "total_decided": total_labeled,
        "overall_false_break_rate_pct": overall_rate,
        "reliable": total_labeled >= MIN_SAMPLE_FOR_RATE,
        "min_sample_for_rate": MIN_SAMPLE_FOR_RATE,
        "by_strategy": strategy_breakdown,
        "by_grade": grade_breakdown,
        "trap_risk_heuristic_check": {
            "n_scored": scored_n,
            "by_bucket": bucket_breakdown,
            "reliable": heuristic_reliable,
            "min_sample_per_bucket": MIN_SAMPLE_PER_BUCKET,
            "note": (
                "Only signals fired after EB-9 increment 1 deployed carry a real "
                "trap_risk_score -- this sample grows over time. A reliable read needs "
                "a real 'high' bucket false-break rate meaningfully above the 'low' "
                "bucket's; until n_scored is large enough this is not yet checkable."
            ),
        },
        "false_break_definition": (
            f"STOP_HIT within {FALSE_BREAK_FAST_STOP_MIN} minutes of firing. A slower "
            "STOP_HIT (traded correctly for a while, then genuinely failed) is NOT "
            "counted as a false break -- a disclosed scope boundary of this v1 label, "
            "not every STOP_HIT."
        ),
    }
