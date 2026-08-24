"""EBIE EB-14 -- champion/challenger promotion review infrastructure.

Per docs/EBIE-BLUEPRINT.md Section 39 ("Maintain champion_model,
challenger_model. Both evaluate the same live candidates. Only champion
drives the verdict.") and docs/EBIE-IMPLEMENTATION-ANSWERS.md Q5.3
("Evaluate weekly; promote manually at first"): the existing scanner
(conviction_score + suppression gate) is champion today, and has been
this entire session -- it alone drives real signals/alerts. EBIE (the
Unified Verdict Engine, EB-8 onward) is the challenger, running in pure
shadow, exactly as every EBIE phase has since EB-1.

This module is the WEEKLY REVIEW step: it classifies EB-13's shadow-
validation report into an honest readiness bucket and persists a
durable snapshot, so a human has real trend data to look at when they
eventually decide whether to promote. It does NOT flip anything --
there is no code path anywhere in this codebase, in this module or any
caller of it, that changes which model drives a live signal. Per
Non-Negotiable Rule #18 ("No silent model/threshold promotion") and
Rule #16 ("No live-capital auto execution"): promotion of any kind
requires a human to explicitly record a decision (human_decision on the
persisted row), which nothing automated in this codebase ever writes.

evaluate_promotion_readiness() is a pure classification of the CURRENT
evidence -- 'READY_FOR_HUMAN_REVIEW' means "the data would no longer be
premature to look at," not "promote now." Given the real live state
confirmed when this was built (1 real episode vs. the required 300),
this will honestly return 'NOT_READY' for a long time yet, and that is
the CORRECT behavior, not a bug to work around.
"""

from __future__ import annotations

import json
from typing import Any

Payload = dict[str, Any]


def evaluate_promotion_readiness(report: Payload) -> Payload:
    """Pure function: classifies an already-computed shadow-validation
    report (api/shadow_validation.py's compute_shadow_validation_report()
    output) into a readiness bucket + the real reasons behind it."""
    gate_a = report.get("gate_a_offline_replay") or {}
    gate_b = report.get("gate_b_live_shadow") or {}
    precision = report.get("precision_comparison") or {}
    false_break = report.get("false_break_rate") or {}
    calibration = report.get("calibration") or {}

    reasons: list[str] = []
    gate_a_met = bool(gate_a.get("meets_preferred_minimum"))
    gate_b_met = bool(gate_b.get("ready_for_promotion_review"))
    precision_available = bool(precision.get("available"))
    precision_favors_ebie = precision.get("favors_ebie") if precision_available else None
    false_break_reliable = bool(false_break.get("reliable"))
    calibration_reliable = bool(calibration.get("model_reliable"))

    if not gate_a_met:
        reasons.append(
            f"Gate A: only {gate_a.get('labeled_episode_count', 0)} labeled episodes "
            f"(preferred >= {gate_a.get('preferred_minimum', 2000)})."
        )
    if not gate_b_met:
        if not gate_b.get("meets_episode_minimum"):
            reasons.append(
                f"Gate B: only {gate_b.get('episode_count', 0)} live-shadow episodes "
                f"(required >= {gate_b.get('min_episodes_required', 300)})."
            )
        if not gate_b.get("meets_session_minimum"):
            reasons.append(
                f"Gate B: only {gate_b.get('session_count', 0)} trading sessions "
                f"(required >= {gate_b.get('min_sessions_required', 25)})."
            )
        if not gate_b.get("both_directions_represented"):
            reasons.append("Gate B: bullish and bearish episodes are not both represented yet.")
        if gate_b.get("symbol_dominance_flagged"):
            reasons.append(
                f"Gate B: {gate_b.get('dominant_symbol')} accounts for "
                f"{gate_b.get('dominant_symbol_share_pct')}% of episodes -- too concentrated."
            )
    if not precision_available:
        reasons.append(
            f"Precision comparison unavailable: {precision.get('reason', 'insufficient sample')}."
        )
    elif not precision_favors_ebie:
        reasons.append(
            f"Precision comparison does not yet favor EBIE "
            f"({precision.get('ebie_actionable_precision_pct')}% vs baseline "
            f"{precision.get('baseline_precision_pct')}%)."
        )
    if not false_break_reliable:
        reasons.append(
            "False-break rate baseline is not yet reliable (insufficient decided sample)."
        )
    if not calibration_reliable:
        reasons.append("ML classifier calibration is not yet reliable.")

    ready = (
        gate_a_met
        and gate_b_met
        and precision_available
        and precision_favors_ebie
        and false_break_reliable
        and calibration_reliable
    )
    readiness = "READY_FOR_HUMAN_REVIEW" if ready else "NOT_READY"
    if not reasons:
        reasons.append(
            "All tracked criteria currently met -- still requires an explicit human decision to promote anything."
        )

    return {
        "readiness": readiness,
        "reasons": reasons,
        "gate_a_met": gate_a_met,
        "gate_b_met": gate_b_met,
        "precision_available": precision_available,
        "precision_favors_ebie": precision_favors_ebie,
        "false_break_reliable": false_break_reliable,
        "calibration_reliable": calibration_reliable,
    }


async def record_promotion_review(pool: Any, report: Payload) -> Payload:
    """Persists one weekly review snapshot. Never writes human_decision
    -- that column stays NULL until a person explicitly sets it via a
    separate, deliberate action outside this automated path."""
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available."}

    evaluation = evaluate_promotion_readiness(report)
    gate_a = report.get("gate_a_offline_replay") or {}
    gate_b = report.get("gate_b_live_shadow") or {}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ebie_promotion_reviews
                (episode_count, session_count, gate_a_met, gate_b_met,
                 precision_available, precision_favors_ebie, false_break_reliable,
                 calibration_reliable, readiness, readiness_reasons, report_snapshot)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id, reviewed_at
            """,
            gate_a.get("labeled_episode_count", 0),
            gate_b.get("session_count", 0),
            evaluation["gate_a_met"],
            evaluation["gate_b_met"],
            evaluation["precision_available"],
            evaluation["precision_favors_ebie"],
            evaluation["false_break_reliable"],
            evaluation["calibration_reliable"],
            evaluation["readiness"],
            evaluation["reasons"],
            json.dumps(report),
        )

    return {
        "available": True,
        "review_id": row["id"],
        "reviewed_at": row["reviewed_at"].isoformat(),
        **evaluation,
    }


async def fetch_promotion_review_history(pool: Any, limit: int = 20) -> Payload:
    if not pool:
        return {
            "available": False,
            "reason": "Postgres analytics pool is not available.",
            "reviews": [],
        }

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, reviewed_at, episode_count, session_count, gate_a_met, gate_b_met,
                   precision_available, precision_favors_ebie, false_break_reliable,
                   calibration_reliable, readiness, readiness_reasons,
                   human_decision, human_decision_note, human_decision_at
            FROM ebie_promotion_reviews
            ORDER BY reviewed_at DESC
            LIMIT $1
            """,
            limit,
        )

    return {
        "available": True,
        "count": len(rows),
        "reviews": [
            {
                "review_id": r["id"],
                "reviewed_at": r["reviewed_at"].isoformat(),
                "episode_count": r["episode_count"],
                "session_count": r["session_count"],
                "gate_a_met": r["gate_a_met"],
                "gate_b_met": r["gate_b_met"],
                "precision_available": r["precision_available"],
                "precision_favors_ebie": r["precision_favors_ebie"],
                "false_break_reliable": r["false_break_reliable"],
                "calibration_reliable": r["calibration_reliable"],
                "readiness": r["readiness"],
                "readiness_reasons": r["readiness_reasons"],
                "human_decision": r["human_decision"],
                "human_decision_note": r["human_decision_note"],
                "human_decision_at": r["human_decision_at"].isoformat()
                if r["human_decision_at"]
                else None,
            }
            for r in rows
        ],
    }
