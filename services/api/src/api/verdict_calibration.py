"""EBIE EB-15 Phase 6 item 10 -- Verdict-Score Calibration.

Per the directive: "Do not display precise probabilities until
calibrated... Minimum promotion gate: 300 unique EBIE episodes plus 25
trading sessions." EB-10 (earlier this session) already built the real
calibration machinery (Platt scaling, isotonic regression, Brier score,
ECE, reliability curve -- api/calibration.py) and applied it to the ML
classifier's own score, but explicitly checked and disclosed that the
Verdict Engine's own directional_score (EB-8) had far too few decided,
verdict-scored episodes to calibrate at the time ("2 real decided
signals... far too few"). This module is that deferred follow-up,
applying the SAME already-proven calibration functions to the verdict
score specifically, gated on the directive's own stricter 300+25
threshold (not just api/calibration.py's own generic 60-row floor,
which stays as a secondary safety net inside calibrate_and_validate()
itself).

Deliberately reuses api/shadow_validation.py's own _fetch_episodes()/
_gate_b() rather than re-deriving "how many real EBIE episodes exist" a
second, potentially-diverging way -- an "EBIE episode" means the exact
same thing here as it does for EB-13's shadow-validation report (a real
decided TARGET_HIT/STOP_HIT signal carrying a genuine sub_scores.verdict
from the Unified Verdict Engine, not a pre-EB-8 archived row).

This is a read-only, on-demand diagnostic (same "computed on request,
not swept/cached, checked occasionally" shape as
compute_shadow_validation_report() -- calibration readiness is not
something that needs polling), not a scheduled retrain. Once the real
sample actually clears the gate, promoting this to a scheduled job (like
ml_classifier's own daily retrain) is a natural, easy follow-up -- not
built now since there is nothing yet to schedule.
"""

from __future__ import annotations

import json

from api.calibration import calibrate_and_validate
from api.shadow_validation import GATE_B_MIN_EPISODES, GATE_B_MIN_SESSIONS, _fetch_episodes, _gate_b


def _decode_json(raw) -> dict:
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


async def compute_verdict_calibration(pool) -> dict:
    """Real, honest answer to "is the Verdict Engine's own directional_score
    calibratable yet?" -- against the directive's own literal 300-episode/
    25-session gate, not a softer proxy. Returns available=False with the
    real current gate numbers when not ready (the expected, honest answer
    today); only actually fits Platt/isotonic once the gate is genuinely
    cleared.
    """
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available."}

    try:
        rows = await _fetch_episodes(pool)
    except Exception as exc:
        return {"available": False, "reason": f"episode query failed: {exc}"}

    rows = list(rows)
    gate = _gate_b(rows)

    if not gate["ready_for_promotion_review"]:
        return {
            "available": False,
            "reason": (
                f"Need {GATE_B_MIN_EPISODES} real EBIE episodes across {GATE_B_MIN_SESSIONS} "
                f"trading sessions before the verdict score can be honestly calibrated -- "
                f"currently {gate['episode_count']} episodes across {gate['session_count']} sessions."
            ),
            "gate": gate,
        }

    scores: list[float] = []
    labels: list[float] = []
    for r in rows:
        sub_scores = _decode_json(r.get("sub_scores"))
        verdict = sub_scores.get("verdict") or {}
        directional = verdict.get("directional_score")
        if directional is None:
            continue
        # calibration.py's own functions (compute_ece's bucketing
        # specifically) assume a 0-1 scale, matching ml_classifier.py's
        # own predict_proba() convention -- directional_score is 0-100,
        # so it's normalized here, once, at the one call site that needs
        # it, rather than changing what 0-100 means everywhere else this
        # session already reads it that way (dashboard, archived rows).
        scores.append(float(directional) / 100.0)
        labels.append(1.0 if r.get("outcome_label") == "TARGET_HIT" else 0.0)

    calibration = calibrate_and_validate(scores, labels)
    return {
        "available": True,
        "gate": gate,
        "n_episodes_with_verdict_score": len(scores),
        **calibration,
    }
