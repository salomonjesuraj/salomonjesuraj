"""Pure statistics helpers -- Phase 13.8: Deflated Sharpe Ratio (DSR) and
per-feature Information Coefficient (IC).

Both operate on the same R-multiple convention: a trade's outcome
expressed in units of risk taken, +risk_reward_ratio for a TARGET_HIT (the
planned reward, the same convention risk_reward_ratio already encodes
everywhere else in this codebase) and -1.0 for a STOP_HIT (a stop-out is a
loss of exactly 1R by definition). This is a real, if coarse,
discretization -- it assumes the exit happened exactly at the planned
target/stop rather than tracking the actual fill, matching the same level
of precision compute_walkforward()'s existing TARGET_HIT/STOP_HIT
classification already works at. EXPIRED rows carry no clean R-multiple
and are excluded, same scope as compute_walkforward()'s own query.

No numpy/scipy dependency -- Python 3.8+'s stdlib `statistics.NormalDist`
already provides both the normal CDF and its inverse (probit), which is
all DSR needs.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

_EULER_MASCHERONI = 0.5772156649015329
_NORMAL = NormalDist()


def r_multiple(outcome_label: str | None, risk_reward_ratio: float | None) -> float | None:
    """+planned R:R for a win, -1.0 for a loss, None for anything else
    (EXPIRED, missing outcome)."""
    if outcome_label == "TARGET_HIT":
        rr = float(risk_reward_ratio or 0)
        return (
            rr if rr > 0 else 1.0
        )  # a stored 0/missing R:R still means "won", fall back to a flat 1R
    if outcome_label == "STOP_HIT":
        return -1.0
    return None


def sharpe_stats(r_multiples: list[float]) -> dict[str, Any]:
    """Sample mean/std/Sharpe/skewness/(non-excess) kurtosis of a list of
    R-multiples. kurtosis here is the raw (not excess) fourth standardized
    moment -- a normal distribution has kurtosis 3, matching the
    convention Bailey & Lopez de Prado's PSR formula expects."""
    n = len(r_multiples)
    if n < 2:
        return {"n": n, "mean": None, "std": None, "sharpe": None, "skew": None, "kurtosis": None}
    mean = sum(r_multiples) / n
    variance = sum((x - mean) ** 2 for x in r_multiples) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    sharpe = (mean / std) if std > 0 else None
    if std > 0:
        skew = (sum((x - mean) ** 3 for x in r_multiples) / n) / (std**3)
        kurtosis = (sum((x - mean) ** 4 for x in r_multiples) / n) / (std**4)
    else:
        skew, kurtosis = 0.0, 3.0
    return {
        "n": n,
        "mean": round(mean, 4),
        "std": round(std, 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "skew": round(skew, 4),
        "kurtosis": round(kurtosis, 4),
    }


def expected_max_sharpe(trial_sharpes: list[float]) -> float | None:
    """E[max SR] across N independently-evaluated trials (Bailey & Lopez
    de Prado 2014's Euler-Mascheroni approximation) -- the "you'd expect
    to see a Sharpe this high from pure luck alone once you've tried N
    variants" benchmark. Grows with both N (more trials, higher expected
    max-by-chance) and the variance of Sharpes across those trials.
    """
    valid = [s for s in trial_sharpes if s is not None and math.isfinite(s)]
    n = len(valid)
    if n < 2:
        return None
    mean_sr = sum(valid) / n
    var_sr = sum((s - mean_sr) ** 2 for s in valid) / (n - 1)
    if var_sr <= 0:
        return mean_sr
    std_sr = math.sqrt(var_sr)
    z1 = _NORMAL.inv_cdf(_clamp_prob(1 - 1.0 / n))
    z2 = _NORMAL.inv_cdf(_clamp_prob(1 - 1.0 / (n * math.e)))
    return std_sr * ((1 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2)


def _clamp_prob(p: float) -> float:
    return max(min(p, 0.999999), 0.000001)


def probabilistic_sharpe_ratio(
    sr_hat: float, sr_benchmark: float, n: int, skew: float, kurtosis: float
) -> float | None:
    """P(true Sharpe > sr_benchmark), given an observed Sharpe sr_hat over
    n trades with the given skew/kurtosis (Bailey & Lopez de Prado 2012).
    When sr_benchmark is expected_max_sharpe() across N trials, this exact
    quantity IS the Deflated Sharpe Ratio -- the probability the winning
    profile's edge is real skill, not just the best-performing draw out of
    N tries."""
    if n < 2:
        return None
    denom = math.sqrt(max(1 - skew * sr_hat + (kurtosis - 1) / 4 * sr_hat**2, 1e-9))
    z = (sr_hat - sr_benchmark) * math.sqrt(n - 1) / denom
    return _NORMAL.cdf(z)


def pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Standard Pearson correlation. For a binary xs (0/1 truthiness
    encoding of a features_snapshot field), this is exactly the
    point-biserial correlation -- the right tool for "does this boolean-
    ish field's presence correlate with better/worse outcomes."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)
