"""EBIE EB-10A -- probability calibration framework.

Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q2.6: "Early EBIE must NOT
label an uncalibrated score as a probability... Calibration is a
standalone deliverable and verification gate." This module is that
framework -- Platt scaling, isotonic regression (Pool Adjacent
Violators Algorithm, PAVA), Brier score, expected calibration error
(ECE), and a reliability-curve/bucket-outcome table. Pure functions, no
new dependencies (Platt scaling reuses ml_classifier.py's own
train_logistic_regression as a single-feature fit -- calibration IS
just a 1D logistic regression of outcome ~ raw_score, no separate
optimizer needed).

Per Non-Negotiable Rule #9 ("No model training in an API request
path"): fitting a calibration mapping is a form of training and must
only run inside the same scheduled/offline retrain flow the raw
classifier already uses (api/ml_classifier.py's train_classifier(),
called by scheduler's daily loop) -- never inside a live HTTP request
handler. This module's fit_* functions are pure/CPU-bound and are
called from that same asyncio.to_thread() worker, not from a route.

Per Q2.6's "out-of-sample calibration verification" requirement: a
calibration mapping fit and evaluated on the SAME rows would be
in-sample for the calibration step even if the underlying raw score was
already out-of-sample for the classifier itself. calibrate_and_validate()
below further splits the classifier's own held-out test set into a
calibration-fit half and a calibration-validation half (chronologically
ordered, matching this whole codebase's time-series-safe splitting
convention) so the reported Brier/ECE/reliability numbers are honestly
out-of-sample for the calibration mapping too, not just for the raw
model.
"""

from __future__ import annotations

import math
from typing import Any

MIN_CALIBRATION_SAMPLE = 60  # needs enough rows on each side of the fit/validate split to be honest
MIN_RELIABILITY_BUCKET = (
    5  # a reliability-curve bucket with fewer real rows than this is too noisy to show
)
Payload = dict[str, Any]


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def fit_platt_scaling(scores: list[float], labels: list[float]) -> Payload:
    """Platt scaling: P(y=1|s) = sigmoid(w*s + b), fit via the SAME
    logistic-regression trainer ml_classifier.py already uses and has
    already been verified with -- calibration is just a 1-feature
    logistic regression, not a separate algorithm needing its own
    implementation."""
    from api.ml_classifier import train_logistic_regression

    X = [[s] for s in scores]
    weights, bias, _losses = train_logistic_regression(X, labels)
    return {"weight": weights[0], "bias": bias}


def apply_platt(score: float, params: Payload) -> float:
    return _sigmoid(float(params["weight"]) * score + float(params["bias"]))


def fit_isotonic_regression(scores: list[float], labels: list[float]) -> list[Payload]:
    """Pool Adjacent Violators Algorithm (PAVA) -- fits a monotonically
    non-decreasing step function mapping raw score -> calibrated
    probability. Returns a list of {score, value} breakpoints, sorted
    by score ascending, ready for apply_isotonic()'s step lookup.
    """
    if not scores:
        return []
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    sorted_scores = [scores[i] for i in order]
    sorted_labels = [labels[i] for i in order]

    # Each block starts as a single point (score, mean=label, weight=1);
    # PAVA merges adjacent blocks whenever a later block's mean would be
    # LOWER than an earlier one's (a monotonicity violation), replacing
    # both with their weighted-average mean, and repeats until the whole
    # sequence is non-decreasing.
    blocks = [
        {"score": s, "mean": y, "weight": 1.0}
        for s, y in zip(sorted_scores, sorted_labels, strict=False)
    ]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i]["mean"] > blocks[i + 1]["mean"]:
            merged_weight = blocks[i]["weight"] + blocks[i + 1]["weight"]
            merged_mean = (
                blocks[i]["mean"] * blocks[i]["weight"]
                + blocks[i + 1]["mean"] * blocks[i + 1]["weight"]
            ) / merged_weight
            blocks[i : i + 2] = [
                {"score": blocks[i + 1]["score"], "mean": merged_mean, "weight": merged_weight}
            ]
            i = max(i - 1, 0)
        else:
            i += 1

    return [{"score": b["score"], "value": b["mean"]} for b in blocks]


def apply_isotonic(score: float, breakpoints: list[Payload]) -> float:
    """Step-function lookup: the calibrated value for `score` is the
    value of the last breakpoint whose own score is <= it (the
    fitted function is a right-continuous step, per PAVA's own
    construction); below the first breakpoint, use its value; above the
    last, use its value (constant extrapolation, not extended fitting)."""
    if not breakpoints:
        return 0.5
    if score <= breakpoints[0]["score"]:
        return float(breakpoints[0]["value"])
    if score >= breakpoints[-1]["score"]:
        return float(breakpoints[-1]["value"])
    value = float(breakpoints[0]["value"])
    for bp in breakpoints:
        if bp["score"] > score:
            break
        value = float(bp["value"])
    return value


def compute_brier_score(probs: list[float], labels: list[float]) -> float | None:
    if not probs:
        return None
    return sum((p - y) ** 2 for p, y in zip(probs, labels, strict=False)) / len(probs)


def compute_ece(probs: list[float], labels: list[float], n_bins: int = 10) -> float | None:
    """Expected Calibration Error: bins predictions by confidence,
    weighted-averages |predicted - actual| across bins by bin size."""
    if not probs:
        return None
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, labels, strict=False):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    total = len(probs)
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        avg_pred = sum(p for p, _ in bucket) / len(bucket)
        avg_actual = sum(y for _, y in bucket) / len(bucket)
        ece += (len(bucket) / total) * abs(avg_pred - avg_actual)
    return ece


def compute_reliability_curve(
    probs: list[float], labels: list[float], n_bins: int = 10
) -> list[Payload]:
    """The probability-bucket-outcome table Q2.6 explicitly requires:
    for each confidence bucket, how many rows landed there, the average
    predicted probability, and the REAL observed outcome rate. A bucket
    with fewer than MIN_RELIABILITY_BUCKET real rows is still listed
    (never silently dropped) but flagged unreliable rather than
    reported as if it were a solid read."""
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, labels, strict=False):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    curve: list[Payload] = []
    for i, bucket in enumerate(bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not bucket:
            curve.append(
                {
                    "bucket": f"{lo:.1f}-{hi:.1f}",
                    "n": 0,
                    "avg_predicted": None,
                    "actual_rate": None,
                    "reliable": False,
                }
            )
            continue
        avg_pred = sum(p for p, _ in bucket) / len(bucket)
        actual_rate = sum(y for _, y in bucket) / len(bucket)
        curve.append(
            {
                "bucket": f"{lo:.1f}-{hi:.1f}",
                "n": len(bucket),
                "avg_predicted": round(avg_pred, 4),
                "actual_rate": round(actual_rate, 4),
                "reliable": len(bucket) >= MIN_RELIABILITY_BUCKET,
            }
        )
    return curve


def calibrate_and_validate(scores: list[float], labels: list[float]) -> Payload:
    """Splits (scores, labels) -- already the classifier's own held-out
    TEST set, itself out-of-sample from training -- chronologically in
    half: first half fits both Platt and isotonic, second half validates
    them out-of-sample (per Q2.6's explicit requirement). Compares Brier
    score on the validation half and recommends whichever is lower,
    reporting both per Q2.6's "Platt scaling and/or isotonic comparison."
    Honest insufficient-sample handling: below MIN_CALIBRATION_SAMPLE
    total, returns available=False rather than fitting on too little
    data.
    """
    n = len(scores)
    if n < MIN_CALIBRATION_SAMPLE:
        return {
            "available": False,
            "reason": f"Need at least {MIN_CALIBRATION_SAMPLE} held-out rows to calibrate ({n} available).",
        }

    split = n // 2
    fit_scores, fit_labels = scores[:split], labels[:split]
    val_scores, val_labels = scores[split:], labels[split:]

    if len(fit_scores) < 20 or len(val_scores) < 20:
        return {
            "available": False,
            "reason": f"Calibration fit/validation split too small (fit={len(fit_scores)}, validate={len(val_scores)}).",
        }

    platt_params = fit_platt_scaling(fit_scores, fit_labels)
    isotonic_breakpoints = fit_isotonic_regression(fit_scores, fit_labels)

    platt_val_probs = [apply_platt(s, platt_params) for s in val_scores]
    isotonic_val_probs = [apply_isotonic(s, isotonic_breakpoints) for s in val_scores]

    # Raw (uncalibrated) score treated as a naive "probability" for
    # comparison -- this is exactly the "score 84 = 84% probability"
    # the blueprint says NOT to do; showing its own Brier/ECE alongside
    # the calibrated methods makes the improvement (or lack of one)
    # concrete rather than asserted.
    raw_brier = compute_brier_score(val_scores, val_labels)
    raw_ece = compute_ece(val_scores, val_labels)

    platt_brier = compute_brier_score(platt_val_probs, val_labels)
    platt_ece = compute_ece(platt_val_probs, val_labels)
    isotonic_brier = compute_brier_score(isotonic_val_probs, val_labels)
    isotonic_ece = compute_ece(isotonic_val_probs, val_labels)

    candidates = [
        ("platt", platt_brier),
        ("isotonic", isotonic_brier),
    ]
    recommended = min(candidates, key=lambda kv: kv[1] if kv[1] is not None else float("inf"))[0]
    recommended_probs = platt_val_probs if recommended == "platt" else isotonic_val_probs

    return {
        "available": True,
        "n_fit": len(fit_scores),
        "n_validate": len(val_scores),
        "raw_score_brier": round(raw_brier, 4) if raw_brier is not None else None,
        "raw_score_ece": round(raw_ece, 4) if raw_ece is not None else None,
        "platt": {
            "params": {k: round(float(v), 6) for k, v in platt_params.items()},
            "brier": round(platt_brier, 4) if platt_brier is not None else None,
            "ece": round(platt_ece, 4) if platt_ece is not None else None,
        },
        "isotonic": {
            "breakpoints": [
                {"score": round(b["score"], 4), "value": round(b["value"], 4)}
                for b in isotonic_breakpoints
            ],
            "brier": round(isotonic_brier, 4) if isotonic_brier is not None else None,
            "ece": round(isotonic_ece, 4) if isotonic_ece is not None else None,
        },
        "recommended_method": recommended,
        "reliability_curve": compute_reliability_curve(recommended_probs, val_labels),
        "note": (
            "Fit on the first half of the classifier's own held-out test set, validated "
            "out-of-sample on the second half -- both Brier score and ECE are genuinely "
            "unseen-data numbers, not in-sample fit quality. Lower Brier/ECE is better; "
            "raw_score_brier/ece shows what an UNCALIBRATED score would look like if wrongly "
            "shown as a probability, for direct comparison."
        ),
    }
