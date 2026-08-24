"""Trained ML classifier over Infusion's archived signal outcomes.

Sequenced deliberately after Phase 13.3 (purged walk-forward CV): this
reuses that exact split (api.routes.backtest._purge_and_embargo), not a
second copy of it, so the classifier is evaluated under the same
leakage-hardened train/test boundary every rule-based precision claim in
this codebase already is.

Real finding that reshaped the feature set, checked against Postgres
before writing any training code: every Phase 1-13 informational field
(fib_targets, ma_regime, chart_patterns, wyckoff_*, donchian_fresh_*,
order_block_*, vcp_score, alignment_*, kelly, rsi_divergence_*, ...) has
single-digit-to-zero presence across the real 12,106-row decided archive
-- most of it predates the currently-deployed code that actually
populates each field (this session redeployed the scanner dozens of
times; a row only carries a field if the scanner build running when it
fired already computed it). Only the CORE signal metadata columns
(conviction_score, risk_reward_ratio, session_hour, conviction_grade,
strategy, sector_id, market_regime, pre_breakout_state) are ~100%
populated today. Rather than hand-pick a feature list that's honest only
right now, the informational fields are wired in generically with a
coverage gate (MIN_INFORMATIONAL_COVERAGE) -- every retrain re-checks
real presence in that run's training set, so a field silently joins the
model once enough real trading days have accumulated under it, with zero
code change needed later. Today, expect none of them to clear the gate;
that's the correct, honest state of the real archive, not a bug.

Model: L2-regularized logistic regression, plain Python (no numpy/
sklearn -- same dependency-avoidance convention as statistics_utils.py's
hand-rolled DSR/PSR). Batch gradient descent with momentum, trained in a
worker thread (api/routes/backtest.py's route handler wraps this in
asyncio.to_thread) so a multi-second training pass never blocks the
event loop.

Governance: informational only, same as every Phase 13 field -- the
trained probability is exposed for review (this module's metrics report
its test-set AUC against the raw conviction_score's OWN AUC on the same
held-out rows, the real "does this add anything over what we already
have" check) and, once trained, may be surfaced into features_snapshot
as ml_probability by scanner/ml_score.py -- never wired into
suppression, position sizing, or the live conviction score itself.
"""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from typing import Any

Payload = dict[str, Any]

# api.routes.backtest imports train_classifier/read_cached_model FROM this
# module (to expose them as routes), so importing it back here at module
# level would be circular and fail at startup depending on which module
# happens to get imported first. Every use below is a function-local import
# instead -- by the time any of these functions actually runs, app startup
# has already finished and both modules are fully loaded either way, so this
# costs nothing at runtime and just avoids the load-time cycle.

MODEL_KEY = "infusion:ml-classifier:model"

MIN_CATEGORY_COUNT = 30  # a categorical value needs this many training rows to get its own dummy; below it, folds into the baseline (most-frequent) category
MIN_INFORMATIONAL_COVERAGE = 30  # a Phase 1-13 informational field needs this many non-absent training rows before the model trusts it at all
MIN_TRAIN = 200
MIN_TEST = 60

L2_LAMBDA = 2.0
LEARNING_RATE = 0.35
MOMENTUM = 0.9
N_ITERS = 400

CORE_CATEGORICAL_COLUMNS = [
    "session_hour",
    "strategy",
    "sector_id",
    "market_regime",
    "pre_breakout_state",
]
GRADE_ORDER = {"D": 0.0, "C": 1.0, "B": 2.0, "A": 3.0, "A+": 4.0}


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------


async def _fetch_rows(pool: Any, days: int) -> list[Payload]:
    from api.routes.backtest import _decode_json_column

    async with pool.acquire() as conn:
        records = await conn.fetch(
            """
            SELECT
                created_at, target_hit_at, stop_hit_at, outcome_label,
                COALESCE(conviction_score, 0)::float AS conviction_score,
                COALESCE(risk_reward_ratio, 0)::float AS risk_reward_ratio,
                COALESCE(session_hour, 'unknown') AS session_hour,
                COALESCE(conviction_grade, '-') AS conviction_grade,
                COALESCE(strategy, '-') AS strategy,
                COALESCE(sector_id, '-') AS sector_id,
                COALESCE(market_regime, '-') AS market_regime,
                COALESCE(pre_breakout_state, '-') AS pre_breakout_state,
                features, sub_scores
            FROM signals
            WHERE created_at >= now() - ($1::int * interval '1 day')
              AND outcome_label IN ('TARGET_HIT', 'STOP_HIT')
              AND NOT COALESCE(suppressed, false)
            ORDER BY created_at ASC
            """,
            days,
        )
    rows: list[Payload] = []
    for r in records:
        d = dict(r)
        d["features"] = _decode_json_column(d.get("features"))
        d["sub_scores"] = _decode_json_column(d.get("sub_scores"))
        rows.append(d)
    return rows


# ---------------------------------------------------------------------------
# Feature spec construction (train-set only -- every stat below is derived
# exclusively from train_rows, never test_rows, or the held-out set would
# leak into the encoding itself)
# ---------------------------------------------------------------------------


def _category_counts(rows: list[Payload], column: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        v = str(r.get(column) or "-")
        counts[v] = counts.get(v, 0) + 1
    return counts


def _mean_std(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(var)


def build_feature_spec(train_rows: list[Payload]) -> list[Payload]:
    """One entry per active model input, in the fixed order weights are
    trained/applied against. Every stat (mean/std, baseline category,
    which informational fields clear the coverage gate) comes from
    train_rows only.
    """
    from api.routes.backtest import (
        CONTINUOUS_IC_FIELDS,
        KNOWN_ABLATION_FIELDS,
        KNOWN_ABLATION_FIELDS_SUB_SCORES,
        _ic_encode,
    )

    spec: list[Payload] = []

    # Core continuous.
    for col in ("conviction_score", "risk_reward_ratio"):
        vals = [float(r.get(col) or 0.0) for r in train_rows]
        mean, std = _mean_std(vals)
        spec.append(
            {"name": col, "kind": "core_continuous", "column": col, "mean": mean, "std": std}
        )

    # Conviction grade -- ordinal, not one-hot (it's a real order: D<C<B<A<A+).
    grade_vals = [GRADE_ORDER.get(str(r.get("conviction_grade") or ""), 1.0) for r in train_rows]
    mean, std = _mean_std(grade_vals)
    spec.append(
        {"name": "conviction_grade_ordinal", "kind": "core_grade_ordinal", "mean": mean, "std": std}
    )

    # Core categorical -- one-hot, dropping the most-frequent value as the
    # implicit baseline (avoids perfect collinearity with the intercept),
    # and dropping any value with too few training rows to mean anything
    # (folds into the baseline too -- same MIN_CATEGORY_COUNT convention
    # used everywhere else this session for "enough sample to trust").
    for col in CORE_CATEGORICAL_COLUMNS:
        counts = _category_counts(train_rows, col)
        if not counts:
            continue
        baseline = max(counts, key=lambda k: counts[k])
        for value in sorted(counts):
            if value == baseline or counts[value] < MIN_CATEGORY_COUNT:
                continue
            spec.append(
                {
                    "name": f"{col}={value}",
                    "kind": "core_category",
                    "column": col,
                    "value": value,
                    "baseline": baseline,
                    "train_count": counts[value],
                }
            )

    # Informational fields (Phase 1-13) -- coverage-gated. Boolean/presence
    # fields from features_snapshot and sub_scores, continuous fields
    # (vcp_score) kept on their own raw scale like the core continuous
    # features above. A field that doesn't clear MIN_INFORMATIONAL_COVERAGE
    # this run is simply left out -- it can join a later retrain once real
    # trading days accumulate under it, no code change needed.
    field_specs = [
        (f, "features") for f in KNOWN_ABLATION_FIELDS if f not in CONTINUOUS_IC_FIELDS
    ] + [(f, "sub_scores") for f in KNOWN_ABLATION_FIELDS_SUB_SCORES]
    for field, column in field_specs:
        encoded = [_ic_encode(field, r.get(column, {}).get(field)) for r in train_rows]
        present = [e for e in encoded if e is not None]
        n_present = sum(1 for e in present if e == 1.0)
        n_absent = len(present) - n_present
        if min(n_present, n_absent) < MIN_INFORMATIONAL_COVERAGE:
            continue
        spec.append(
            {
                "name": field,
                "kind": "informational_boolean",
                "column": column,
                "field": field,
                "n_present": n_present,
                "n_absent": n_absent,
            }
        )

    for field in CONTINUOUS_IC_FIELDS:
        vals = [
            float(r.get("features", {}).get(field))
            for r in train_rows
            if r.get("features", {}).get(field) is not None
        ]
        if len(vals) < MIN_INFORMATIONAL_COVERAGE:
            continue
        mean, std = _mean_std(vals)
        spec.append(
            {
                "name": field,
                "kind": "informational_continuous",
                "column": "features",
                "field": field,
                "mean": mean,
                "std": std,
                "n_present": len(vals),
            }
        )

    return spec


def encode_row(row: Payload, spec: list[Payload]) -> list[float]:
    """Turn one row (Postgres training row, or a live scanner candidate's
    equivalent core fields + features_snapshot/sub_scores dicts -- same
    shape either way) into the numeric vector `spec` describes, in order.

    Missing/absent values: a core categorical column simply not matching
    any dummy encodes as all-zero (the baseline case, correct one-hot
    behavior) -- never "missing". A continuous informational field absent
    on this particular row is imputed at the training mean (encodes as
    0.0 post-standardization) rather than guessing a direction.
    """
    from api.routes.backtest import _ic_encode

    out: list[float] = []
    for f in spec:
        kind = f["kind"]
        if kind == "core_continuous":
            raw = float(row.get(f["column"]) or 0.0)
            out.append((raw - f["mean"]) / f["std"] if f["std"] > 0 else 0.0)
        elif kind == "core_grade_ordinal":
            raw = GRADE_ORDER.get(str(row.get("conviction_grade") or ""), 1.0)
            out.append((raw - f["mean"]) / f["std"] if f["std"] > 0 else 0.0)
        elif kind == "core_category":
            out.append(1.0 if str(row.get(f["column"]) or "-") == f["value"] else 0.0)
        elif kind == "informational_boolean":
            encoded = _ic_encode(f["field"], (row.get(f["column"]) or {}).get(f["field"]))
            out.append(1.0 if encoded == 1.0 else 0.0)
        elif kind == "informational_continuous":
            raw_value = (row.get(f["column"]) or {}).get(f["field"])
            if raw_value is None:
                out.append(0.0)  # imputed at the training mean -> 0 after standardization
            else:
                out.append((float(raw_value) - f["mean"]) / f["std"] if f["std"] > 0 else 0.0)
        else:
            out.append(0.0)
    return out


# ---------------------------------------------------------------------------
# Logistic regression -- plain Python, L2-regularized batch gradient
# descent with momentum. Verified against hand-computed cases (see
# scratchpad verify script) before this was wired to real data: gradient
# direction, convergence on a linearly separable toy set, and the AUC
# helper below against a known-correlation synthetic pair.
# ---------------------------------------------------------------------------


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def train_logistic_regression(
    X: list[list[float]],
    y: list[float],
    l2: float = L2_LAMBDA,
    lr: float = LEARNING_RATE,
    momentum: float = MOMENTUM,
    n_iters: int = N_ITERS,
) -> tuple[list[float], float, list[float]]:
    n = len(X)
    d = len(X[0]) if X else 0
    weights = [0.0] * d
    bias = 0.0
    v_w = [0.0] * d
    v_b = 0.0
    losses: list[float] = []

    for _ in range(n_iters):
        grad_w = [0.0] * d
        grad_b = 0.0
        loss = 0.0
        for i in range(n):
            xi = X[i]
            z = bias + sum(weights[j] * xi[j] for j in range(d))
            p = _sigmoid(z)
            err = p - y[i]
            for j in range(d):
                if xi[j] != 0.0:
                    grad_w[j] += err * xi[j]
            grad_b += err
            p_clamped = min(max(p, 1e-12), 1 - 1e-12)
            loss += -(y[i] * math.log(p_clamped) + (1 - y[i]) * math.log(1 - p_clamped))

        for j in range(d):
            grad_w[j] = grad_w[j] / n + (l2 / n) * weights[j]
        grad_b /= n
        loss = loss / n + (l2 / (2 * n)) * sum(w * w for w in weights)
        losses.append(loss)

        for j in range(d):
            v_w[j] = momentum * v_w[j] - lr * grad_w[j]
            weights[j] += v_w[j]
        v_b = momentum * v_b - lr * grad_b
        bias += v_b

    return weights, bias, losses


def predict_proba(X: list[list[float]], weights: list[float], bias: float) -> list[float]:
    d = len(weights)
    return [_sigmoid(bias + sum(weights[j] * row[j] for j in range(d))) for row in X]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def compute_auc(y_true: list[float], y_score: list[float]) -> float | None:
    """Mann-Whitney U / rank-sum AUC -- no external stats library needed.
    Verified against a hand-built perfectly-separable case (AUC=1.0) and a
    fully-inverted case (AUC=0.0) before use.
    """
    n_pos = sum(1 for v in y_true if v == 1.0)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    order = sorted(range(len(y_score)), key=lambda i: y_score[i])
    ranks = [0.0] * len(y_score)
    i = 0
    rank = 1
    while i < len(order):
        j = i
        while j + 1 < len(order) and y_score[order[j + 1]] == y_score[order[i]]:
            j += 1
        avg_rank = (rank + (rank + (j - i))) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        rank += j - i + 1
        i = j + 1
    sum_ranks_pos = sum(ranks[i] for i in range(len(y_true)) if y_true[i] == 1.0)
    u = sum_ranks_pos - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def compute_metrics(y_true: list[float], y_score: list[float], threshold: float = 0.5) -> Payload:
    n = len(y_true)
    tp = sum(1 for i in range(n) if y_score[i] >= threshold and y_true[i] == 1.0)
    fp = sum(1 for i in range(n) if y_score[i] >= threshold and y_true[i] == 0.0)
    tn = sum(1 for i in range(n) if y_score[i] < threshold and y_true[i] == 0.0)
    fn = sum(1 for i in range(n) if y_score[i] < threshold and y_true[i] == 1.0)
    accuracy = (tp + tn) / n if n else None
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    log_loss = None
    brier = None
    if n:
        clamped = [min(max(p, 1e-12), 1 - 1e-12) for p in y_score]
        log_loss = (
            -sum(
                y_true[i] * math.log(clamped[i]) + (1 - y_true[i]) * math.log(1 - clamped[i])
                for i in range(n)
            )
            / n
        )
        brier = sum((y_score[i] - y_true[i]) ** 2 for i in range(n)) / n
    auc_value = compute_auc(y_true, y_score)
    return {
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "log_loss": round(log_loss, 4) if log_loss is not None else None,
        "brier_score": round(brier, 4) if brier is not None else None,
        "auc": round(auc_value, 4) if auc_value is not None else None,
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _label(row: Payload) -> float:
    return 1.0 if row.get("outcome_label") == "TARGET_HIT" else 0.0


def _train_sync(train_rows: list[Payload], test_rows: list[Payload]) -> Payload:
    """The actual CPU-bound work -- run inside asyncio.to_thread() by the
    caller so a multi-second training pass never blocks the event loop.
    """
    spec = build_feature_spec(train_rows)
    X_train = [encode_row(r, spec) for r in train_rows]
    y_train = [_label(r) for r in train_rows]
    X_test = [encode_row(r, spec) for r in test_rows]
    y_test = [_label(r) for r in test_rows]

    weights, bias, losses = train_logistic_regression(X_train, y_train)
    test_scores = predict_proba(X_test, weights, bias)
    test_metrics = compute_metrics(y_test, test_scores)

    # The real honesty check: is the trained model actually better than
    # just using the conviction_score Infusion already computes for every
    # signal? Same held-out test rows, same AUC method, raw score as-is.
    baseline_scores = [float(r.get("conviction_score") or 0.0) for r in test_rows]
    baseline_auc = compute_auc(y_test, baseline_scores)

    majority_class = 1.0 if sum(y_train) / len(y_train) >= 0.5 else 0.0
    majority_accuracy = (
        sum(1 for v in y_test if v == majority_class) / len(y_test) if y_test else None
    )

    reliable = (
        len(train_rows) >= MIN_TRAIN
        and len(test_rows) >= MIN_TEST
        and test_metrics["auc"] is not None
    )

    # `reliable` is purely about sample size (enough rows to trust the
    # measurement itself, same semantics as every other Phase 13
    # reliability flag) -- it says nothing about whether the lift found is
    # actually worth anything. That's a separate, explicit read: real
    # first-run finding against the live archive was lift=+0.012 (test AUC
    # 0.556 vs the existing conviction_score's own 0.544 on the same
    # held-out rows) -- barely above the raw score already in production,
    # confirmed not a bug (the same code correctly finds AUC=0.93 with a
    # real +0.05 lift on a synthetic set where a feature genuinely carries
    # independent signal). With today's coverage-gated feature set (no
    # Phase 1-13 informational field clears MIN_INFORMATIONAL_COVERAGE
    # yet -- see module docstring), there just isn't much left to learn
    # beyond what conviction_score/risk_reward_ratio/grade already encode.
    lift = (
        test_metrics["auc"] - baseline_auc
        if test_metrics["auc"] is not None and baseline_auc is not None
        else None
    )
    if lift is None:
        interpretation = (
            "Baseline AUC unavailable on this test set -- can't compare against the existing score."
        )
    elif lift >= 0.05:
        interpretation = (
            "Meaningfully more predictive than the existing conviction score on held-out data."
        )
    elif lift >= 0.0:
        interpretation = "Roughly matches the existing conviction score -- no clear edge yet."
    else:
        interpretation = "Underperforms the existing conviction score on held-out data -- not worth trusting yet."

    # EBIE EB-10B: probability calibration (api/calibration.py), fit and
    # validated entirely within this SAME offline/scheduled training
    # pass (never inside a live HTTP request, per Non-Negotiable Rule
    # #9 -- calibration fitting is itself a form of training). Uses the
    # classifier's own held-out test_scores/y_test -- already out-of-
    # sample from training -- further split in half so the reported
    # Brier/ECE numbers are genuinely out-of-sample for the calibration
    # mapping too, not just for the raw model (Q2.6's explicit
    # requirement). Per Q2.6's own worked example ("EBIE Score: 82/100"
    # is allowed, "78% breakout probability" is not unless calibration
    # is validated): calibration["available"] gates whether a consumer
    # may ever show test_scores as a percentage probability at all.
    from api.calibration import calibrate_and_validate

    calibration = calibrate_and_validate(test_scores, y_test)

    return {
        "available": True,
        "trained_at": datetime.now(UTC).isoformat(),
        "n_train": len(train_rows),
        "n_test": len(test_rows),
        "n_active_features": len(spec),
        "feature_spec": spec,
        "weights": [round(w, 6) for w in weights],
        "bias": round(bias, 6),
        "final_train_loss": round(losses[-1], 5) if losses else None,
        "test_metrics": test_metrics,
        "baseline_score_auc": round(baseline_auc, 4) if baseline_auc is not None else None,
        "lift_over_score_auc": round(lift, 4) if lift is not None else None,
        "interpretation": interpretation,
        "majority_class_accuracy": round(majority_accuracy, 4)
        if majority_accuracy is not None
        else None,
        "train_win_rate": round(sum(y_train) / len(y_train), 4) if y_train else None,
        "reliable": reliable,
        "calibration": calibration,
    }


async def train_classifier(
    pool: Any, redis: Any, days: int = 400, train_pct: float = 0.70, embargo_min: float = 5.0
) -> Payload:
    """Fetch archived outcomes, apply the same purge/embargo split
    Phase 13.3's walk-forward optimizer uses, train in a worker thread,
    evaluate against the held-out embargoed test set, and cache the
    result. Diagnostic evidence only -- see module docstring.
    """
    import asyncio

    from api.routes.backtest import _purge_and_embargo

    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available."}

    days = max(20, min(730, int(days or 400)))
    rows = await _fetch_rows(pool, days)
    total = len(rows)
    if total < MIN_TRAIN + MIN_TEST:
        result = {
            "available": True,
            "reliable": False,
            "n_total": total,
            "reason": f"Need at least {MIN_TRAIN + MIN_TEST} decided outcomes to train ({total} available).",
        }
        if redis:
            await redis.set(MODEL_KEY, json.dumps(result, separators=(",", ":")), ex=48 * 3600)
        return result

    split = max(MIN_TRAIN, min(total - MIN_TEST, int(total * train_pct)))
    train_rows, test_rows, purged_count, embargoed_count = _purge_and_embargo(
        rows, split, embargo_min
    )
    if len(train_rows) < MIN_TRAIN or len(test_rows) < MIN_TEST:
        result = {
            "available": True,
            "reliable": False,
            "n_total": total,
            "n_train": len(train_rows),
            "n_test": len(test_rows),
            "reason": (
                f"Purging ({purged_count} row(s)) and a {embargo_min:.0f}-min embargo "
                f"({embargoed_count} row(s)) left too few rows on one side of the split "
                f"(train={len(train_rows)}, need {MIN_TRAIN}; test={len(test_rows)}, need {MIN_TEST})."
            ),
        }
        if redis:
            await redis.set(MODEL_KEY, json.dumps(result, separators=(",", ":")), ex=48 * 3600)
        return result

    started = time.monotonic()
    result = await asyncio.to_thread(_train_sync, train_rows, test_rows)
    result["train_wall_seconds"] = round(time.monotonic() - started, 2)
    result["purged_train_count"] = purged_count
    result["embargoed_test_count"] = embargoed_count
    result["days"] = days

    if redis:
        await redis.set(MODEL_KEY, json.dumps(result, separators=(",", ":")), ex=48 * 3600)
    return result


async def read_cached_model(redis: Any) -> Payload:
    if not redis:
        return {"available": False, "reason": "Redis is not available."}
    raw = await redis.get(MODEL_KEY)
    if not raw:
        return {
            "available": False,
            "reason": "No trained model yet -- the daily retrain loop hasn't completed a run.",
        }
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        decoded = json.loads(text)
        return decoded if isinstance(decoded, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {"available": False, "reason": "Cached model failed to decode."}
