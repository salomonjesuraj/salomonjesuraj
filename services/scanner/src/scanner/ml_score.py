"""ML classifier live scoring -- apply the cached, api-trained logistic
regression model (api/ml_classifier.py) to a freshly-fired signal
candidate, at the exact moment engine.py builds it.

Scanner has no Python import path to api's code (separate services,
separate Docker images) so the small amount of encoding logic below is a
deliberate, minimal duplicate of api/ml_classifier.py's encode_row() --
but both sides key off the SAME cached model spec
(infusion:ml-classifier:model, written once daily by the scheduler's
ml_classifier_loop calling api's /api/backtest/ml-classifier route) as the
single source of truth for weights/means/categories. Only the encoding
CODE is duplicated here, never the trained numbers -- there's no risk of
scanner's score disagreeing with what api actually trained, since it's
reading the identical JSON blob and applying the identical arithmetic.

Informational only -- see the model's own `interpretation` field (part of
what this returns) for whether it's actually adding anything over
conviction_score yet. Real first finding, checked live against the
archive: not meaningfully, with today's real feature coverage (see
api/ml_classifier.py's module docstring). Never wired into suppression,
position sizing, or the live conviction score -- sub_scores["ml_classifier"]
only, same category as Kelly sizing and cross_confirmation.
"""

from __future__ import annotations

import datetime
import json
import math

MODEL_KEY = "infusion:ml-classifier:model"
GRADE_ORDER = {"D": 0.0, "C": 1.0, "B": 2.0, "A": 3.0, "A+": 4.0}

# Same session-hour boundaries (IST) as archiver/writer.py's
# _classify_session() -- kept in sync deliberately so a live-scored
# signal's session_hour dummy matches exactly what the archived row it
# becomes will carry (both derive from the same created_at_us moment).
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_SESSION_BOUNDARIES = [
    (9 * 60 + 15, 10 * 60, "opening"),
    (10 * 60, 12 * 60, "mid_morning"),
    (12 * 60, 14 * 60, "midday"),
    (14 * 60, 15 * 60 + 30, "closing"),
]


def classify_session_ist(created_at_us: int) -> str:
    if created_at_us <= 0:
        return "unknown"
    dt = datetime.datetime.fromtimestamp(created_at_us / 1_000_000, tz=_IST)
    t = dt.hour * 60 + dt.minute
    for start, end, label in _SESSION_BOUNDARIES:
        if start <= t < end:
            return label
    return "pre_market" if t < 9 * 60 + 15 else "post_market"


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _ablation_field_present(value) -> bool:
    """Mirrors api/routes/backtest.py's _ablation_field_present exactly."""
    if value is None:
        return False
    if isinstance(value, (dict, list, str)):
        return len(value) > 0
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _ic_encode(field: str, value) -> float | None:
    """Mirrors api/routes/backtest.py's _ic_encode exactly (same ma_regime
    3-way special-case, same 0/1 presence encoding otherwise)."""
    if field == "ma_regime":
        if value == "golden_cross":
            return 1.0
        if value == "death_cross":
            return 0.0
        return None
    return 1.0 if _ablation_field_present(value) else 0.0


def _encode(core: dict, features: dict, sub_scores: dict, spec: list[dict]) -> list[float]:
    """Mirrors api/ml_classifier.py's encode_row() exactly, field-kind by
    field-kind -- same missing-value conventions (a core categorical not
    matching any dummy is the baseline case, 0.0; a missing continuous
    informational field imputes to the training mean, 0.0 post-
    standardization)."""
    out: list[float] = []
    for f in spec:
        kind = f.get("kind")
        if kind == "core_continuous":
            raw = float(core.get(f["column"]) or 0.0)
            std = f.get("std") or 0.0
            out.append((raw - f["mean"]) / std if std > 0 else 0.0)
        elif kind == "core_grade_ordinal":
            raw = GRADE_ORDER.get(str(core.get("conviction_grade") or ""), 1.0)
            std = f.get("std") or 0.0
            out.append((raw - f["mean"]) / std if std > 0 else 0.0)
        elif kind == "core_category":
            out.append(1.0 if str(core.get(f["column"]) or "-") == f["value"] else 0.0)
        elif kind == "informational_boolean":
            source = features if f.get("column") == "features" else sub_scores
            encoded = _ic_encode(f["field"], (source or {}).get(f["field"]))
            out.append(1.0 if encoded == 1.0 else 0.0)
        elif kind == "informational_continuous":
            raw = (features or {}).get(f["field"])
            if raw is None:
                out.append(0.0)
            else:
                std = f.get("std") or 0.0
                out.append((float(raw) - f["mean"]) / std if std > 0 else 0.0)
        else:
            out.append(0.0)
    return out


async def score_signal(redis, *, core: dict, features: dict, sub_scores: dict) -> dict:
    """Best-effort: read the cached model, encode this signal's own core
    metadata + features_snapshot/sub_scores the exact way it was trained,
    and return the probability plus the model's own honesty metrics -- or
    {} on any failure/missing model, same convention as every other
    best-effort Redis read in this codebase (_read_kelly_sizing,
    _fetch_mtf_cache). Never raises into the hot signal-firing path.
    """
    try:
        raw = await redis.get(MODEL_KEY)
        if not raw:
            return {}
        text = raw.decode() if isinstance(raw, bytes) else raw
        model = json.loads(text)
        weights = model.get("weights")
        spec = model.get("feature_spec")
        if not model.get("available") or not weights or not spec:
            return {}
        vector = _encode(core, features or {}, sub_scores or {}, spec)
        bias = float(model.get("bias") or 0.0)
        z = bias + sum(weights[i] * vector[i] for i in range(len(weights)))
        prob = _sigmoid(z)
        test_metrics = model.get("test_metrics") or {}
        return {
            "ml_probability": round(prob, 4),
            "ml_reliable": bool(model.get("reliable")),
            "ml_model_auc": test_metrics.get("auc"),
            "ml_model_lift_over_score": model.get("lift_over_score_auc"),
            "ml_model_interpretation": model.get("interpretation"),
            "ml_model_trained_at": model.get("trained_at"),
        }
    except Exception:
        return {}
