"""EBIE EB-13 -- shadow validation tracking, per docs/EBIE-BLUEPRINT.md
Section 38 ("Every new model/feature should enter SHADOW... Promote
only after sufficient real unseen outcomes") and Section EB-13 ("Run
new system without changing production alerts. Collect unseen
outcomes").

This is the TRACKING/REPORTING infrastructure, not a promotion
decision -- per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q5.2/Q5.3,
promotion is manual, requires real weeks of accumulated evidence, and
"should not be promoted because the project is impatient." Every EBIE
phase (EB-1 through EB-12) has already been running in pure shadow this
entire time -- nothing here changes that. This module answers, with
real numbers, the honest question: "are we there yet?"

Per Q5.2's two-gate structure:

  Gate A (offline/replay): no leakage, no repainting, deterministic
  replay, stable performance across regime slices, preferably >=2,000
  labeled candidate episodes. Several of these properties were already
  built and verified earlier in this session (purged walk-forward CV
  for leakage, the episode-freeze mechanism for no-repaint) -- this
  module reports the REAL labeled-episode count against that
  preference, not re-litigating already-shipped, already-verified
  mechanisms.

  Gate B (live shadow): >=300 unique EBIE episodes AND >=25 trading
  sessions (whichever takes longer), both directions represented, no
  single symbol dominating, no unresolved data-quality/repaint defect.

An "EBIE episode" here is a real decided signal (TARGET_HIT/STOP_HIT)
that carries a real sub_scores.verdict -- i.e. was actually scored by
the EB-8 Unified Verdict Engine, not just an old pre-EB-8 archived row.
"""

from __future__ import annotations

import json
from typing import Any, cast

from api.ml_classifier import read_cached_model
from api.trap_labels import compute_false_break_stats

GATE_B_MIN_EPISODES = 300
GATE_B_MIN_SESSIONS = 25
SYMBOL_DOMINANCE_WARN_PCT = (
    20.0  # any single symbol above this share of episodes is a real concentration flag
)
MIN_COMPARISON_SAMPLE = 30  # don't compare EBIE-verdict vs baseline precision on a tiny sample
Payload = dict[str, Any]
Row = Any


def _decode_json(raw: object) -> Payload:
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    return cast(Payload, decoded) if isinstance(decoded, dict) else {}


ACTIONABLE_VERDICT_BANDS = {"READY", "ARMED_CANDIDATE"}


async def _fetch_episodes(pool: Any) -> list[Row]:
    async with pool.acquire() as conn:
        return list(
            await conn.fetch(
                """
            SELECT symbol, signal_type, outcome_label, sub_scores, created_at,
                   entry_premium_ask, entry_premium_bid, exit_premium_bid
            FROM signals
            WHERE sub_scores ? 'verdict'
              AND outcome_label IN ('TARGET_HIT', 'STOP_HIT')
              AND NOT COALESCE(suppressed, false)
            ORDER BY created_at DESC
            """
            )
        )


def _gate_a(episode_count: int) -> Payload:
    checklist = [
        {
            "item": "Purged walk-forward CV (no leakage across train/test split)",
            "status": "verified",
            "evidence": "Phase 13.3, commit history",
        },
        {
            "item": "Episode-freeze mechanism (no repainting of entry/SL/target within an episode)",
            "status": "verified",
            "evidence": "Phase W + EB-1's EpisodeManager",
        },
        {
            "item": "Deterministic replay (same feature snapshot -> same score)",
            "status": "verified",
            "evidence": "feature_versions/pure scoring functions throughout EB-1..EB-12",
        },
        {
            "item": "Reproducible feature snapshots (sub_scores/features_snapshot archived per signal)",
            "status": "verified",
            "evidence": "archiver JSONB persistence",
        },
    ]
    return {
        "checklist": checklist,
        "labeled_episode_count": episode_count,
        "preferred_minimum": 2000,
        "meets_preferred_minimum": episode_count >= 2000,
        "note": (
            "The architectural properties above were built and verified in earlier EBIE phases, "
            "not re-litigated here. labeled_episode_count is the real, current count of decided "
            "signals carrying a genuine EB-8 verdict score -- 2,000 is a preference, not a hard gate."
        ),
    }


def _gate_b(rows: list[Row]) -> Payload:
    total = len(rows)
    sessions = {r["created_at"].date() for r in rows if r.get("created_at")}
    by_symbol: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    for r in rows:
        by_symbol[r["symbol"]] = by_symbol.get(r["symbol"], 0) + 1
        by_direction[r["signal_type"]] = by_direction.get(r["signal_type"], 0) + 1

    max_symbol_share = round(100 * max(by_symbol.values()) / total, 1) if total else 0.0
    dominant_symbol = max(by_symbol, key=lambda symbol: by_symbol[symbol]) if by_symbol else None
    both_directions = len([d for d in by_direction if by_direction[d] > 0]) >= 2

    meets_episodes = total >= GATE_B_MIN_EPISODES
    meets_sessions = len(sessions) >= GATE_B_MIN_SESSIONS
    meets_dominance = max_symbol_share <= SYMBOL_DOMINANCE_WARN_PCT if total else False
    ready = meets_episodes and meets_sessions and both_directions and meets_dominance

    return {
        "episode_count": total,
        "min_episodes_required": GATE_B_MIN_EPISODES,
        "meets_episode_minimum": meets_episodes,
        "session_count": len(sessions),
        "min_sessions_required": GATE_B_MIN_SESSIONS,
        "meets_session_minimum": meets_sessions,
        "direction_breakdown": by_direction,
        "both_directions_represented": both_directions,
        "dominant_symbol": dominant_symbol,
        "dominant_symbol_share_pct": max_symbol_share,
        "symbol_dominance_flagged": not meets_dominance,
        "ready_for_promotion_review": ready,
        "note": (
            "'ready_for_promotion_review' means the SAMPLE is large and balanced enough to review, "
            "not that promotion is recommended -- that also needs the performance comparison below "
            "to actually favor EBIE, and remains a manual decision per Q5.3 either way."
        ),
    }


def _precision_comparison(rows: list[Row]) -> Payload:
    """Does EBIE's own verdict band actually correlate with a HIGHER
    real precision than the unfiltered baseline, on the exact same
    episode set? The genuine "precision@K" acceptance criterion (Section
    59, item 5) -- K here is "the subset EBIE itself calls actionable
    (READY/ARMED_CANDIDATE)", not an arbitrary top-N."""
    if len(rows) < MIN_COMPARISON_SAMPLE:
        return {
            "available": False,
            "reason": f"Need at least {MIN_COMPARISON_SAMPLE} decided verdict-scored episodes to compare ({len(rows)} available).",
        }

    baseline_hits = sum(1 for r in rows if r["outcome_label"] == "TARGET_HIT")
    baseline_precision = round(100 * baseline_hits / len(rows), 1)

    actionable = []
    for r in rows:
        verdict_raw = _decode_json(r["sub_scores"]).get("verdict")
        verdict_payload = cast(Payload, verdict_raw) if isinstance(verdict_raw, dict) else {}
        verdict = verdict_payload.get("verdict")
        if verdict in ACTIONABLE_VERDICT_BANDS:
            actionable.append(r)

    if len(actionable) < MIN_COMPARISON_SAMPLE:
        return {
            "available": False,
            "reason": (
                f"Only {len(actionable)} decided episodes reached an actionable verdict band "
                f"(READY/ARMED_CANDIDATE) -- need {MIN_COMPARISON_SAMPLE} to compare precision honestly."
            ),
            "baseline_precision_pct": baseline_precision,
            "baseline_n": len(rows),
        }

    actionable_hits = sum(1 for r in actionable if r["outcome_label"] == "TARGET_HIT")
    actionable_precision = round(100 * actionable_hits / len(actionable), 1)

    return {
        "available": True,
        "baseline_precision_pct": baseline_precision,
        "baseline_n": len(rows),
        "ebie_actionable_precision_pct": actionable_precision,
        "ebie_actionable_n": len(actionable),
        "lift_pct_points": round(actionable_precision - baseline_precision, 1),
        "favors_ebie": actionable_precision > baseline_precision,
    }


async def compute_shadow_validation_report(pool: Any, redis: Any, days: int = 90) -> Payload:
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available."}

    rows = await _fetch_episodes(pool)

    gate_a = _gate_a(len(rows))
    gate_b = _gate_b(rows)
    precision = _precision_comparison(rows)
    false_break = await compute_false_break_stats(pool, days=days)
    cached_model = (
        await read_cached_model(redis)
        if redis
        else {"available": False, "reason": "Redis unavailable."}
    )
    model_calibration_raw = cached_model.get("calibration")
    model_calibration = (
        cast(Payload, model_calibration_raw) if isinstance(model_calibration_raw, dict) else {}
    )

    return {
        "available": True,
        "gate_a_offline_replay": gate_a,
        "gate_b_live_shadow": gate_b,
        "precision_comparison": precision,
        "false_break_rate": {
            "overall_rate_pct": false_break.get("overall_false_break_rate_pct"),
            "reliable": false_break.get("reliable"),
            "trap_risk_heuristic_check": false_break.get("trap_risk_heuristic_check"),
        },
        "calibration": {
            "model_available": cached_model.get("available"),
            "model_reliable": cached_model.get("reliable"),
            "calibration_available": model_calibration.get("available")
            if cached_model.get("available")
            else None,
            "platt_ece": (model_calibration.get("platt") or {}).get("ece"),
            "isotonic_ece": (model_calibration.get("isotonic") or {}).get("ece"),
        },
        "promotion_note": (
            "Per Q5.2/Q5.3: promotion is a manual decision requiring both gates satisfied, a "
            "favorable precision/false-break/calibration comparison, and several weeks of real "
            "shadow evidence -- never automated, never rushed."
        ),
    }
