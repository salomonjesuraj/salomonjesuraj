"""EBIE EB-11 (increment 2) -- daily loss budget + consecutive-losses,
the two portfolio-risk metrics that genuinely need archived Postgres
history (real decided outcomes from earlier today / recent sessions),
which scanner has no direct access to. Computed here in `api` on a
periodic sweep and cached to Redis, same "api computes + caches,
scanner reads a cheap key" pattern as VIX multiplier/Kelly sizing/F&O
ban -- see api/vix_sizing.py's own precedent.

Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q2.4: informational/advisory
only while paper-only -- this module never blocks anything; scanner
reads the cached result the same way it reads every other advisory
sub_score.

max_daily_loss is NOT a new invented threshold -- it's the real,
already-user-configurable field in infusion:risk:settings
(services/api/src/api/routes/risk.py, default Rs.2,500), the same
setting scanner's own _recommended_lots() already reads for position
sizing. Reused as-is, not duplicated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

_IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_MAX_DAILY_LOSS = 2500.0
CONSECUTIVE_LOOKBACK = 20  # how many recent decided signals to scan for a losing streak


def _decode_json(raw: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def _streak(labels: list[str]) -> int:
    """How many STOP_HITs in a row, starting from the front of `labels`
    (expected to already be ordered most-recent-first), before hitting
    a TARGET_HIT or running out. Pure, independently testable."""
    streak = 0
    for label in labels:
        if label == "STOP_HIT":
            streak += 1
        else:
            break
    return streak


async def _read_max_daily_loss(redis: Any) -> float:
    if not redis:
        return DEFAULT_MAX_DAILY_LOSS
    try:
        raw = await redis.get("infusion:risk:settings")
        if not raw:
            return DEFAULT_MAX_DAILY_LOSS
        settings = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        return float(settings.get("max_daily_loss") or DEFAULT_MAX_DAILY_LOSS)
    except Exception:
        return DEFAULT_MAX_DAILY_LOSS


async def compute_daily_loss_budget(pool: Any, redis: Any) -> dict[str, Any]:
    """Realized loss today = sum of risk_amount (position_sizing sub_score)
    across today's (IST trading day) real STOP_HIT signals -- each stop-out
    is treated as having realized its own at-risk amount, the same rupee
    figure the position-sizing sub_score already committed to at signal
    time. Never fabricated: a signal missing risk_amount contributes 0,
    not a guessed value."""
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available."}

    today_ist = datetime.now(_IST).date()
    day_start_utc = datetime(
        today_ist.year, today_ist.month, today_ist.day, tzinfo=_IST
    ).astimezone(UTC)

    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT sub_scores FROM signals
                WHERE outcome_label = 'STOP_HIT' AND NOT COALESCE(suppressed, false)
                  AND created_at >= $1
                """,
                day_start_utc,
            )
        except Exception as exc:
            return {"available": False, "reason": f"daily-loss query failed: {exc}"}

    realized_loss = 0.0
    stop_count = 0
    for r in rows:
        sub_scores = _decode_json(r["sub_scores"])
        risk_amount = float((sub_scores.get("position_sizing") or {}).get("risk_amount") or 0.0)
        realized_loss += risk_amount
        stop_count += 1

    max_daily_loss = await _read_max_daily_loss(redis)
    budget_used_pct = round(100 * realized_loss / max_daily_loss, 1) if max_daily_loss > 0 else None

    return {
        "available": True,
        "trading_date": today_ist.isoformat(),
        "realized_loss_today": round(realized_loss, 2),
        "stop_hit_count_today": stop_count,
        "max_daily_loss": max_daily_loss,
        "budget_used_pct": budget_used_pct,
        "budget_exceeded": budget_used_pct is not None and budget_used_pct >= 100.0,
    }


async def compute_consecutive_losses(pool: Any) -> dict[str, Any]:
    """Overall + per-strategy: scanning back from the most recent decided
    signal, how many in a row are STOP_HIT before the first TARGET_HIT
    (or history runs out)? A real, simple momentum-of-losses read, not a
    calibrated probability -- purely a count of what actually happened."""
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available."}

    async with pool.acquire() as conn:
        try:
            overall_rows = await conn.fetch(
                """
                SELECT outcome_label FROM signals
                WHERE outcome_label IN ('TARGET_HIT', 'STOP_HIT') AND NOT COALESCE(suppressed, false)
                ORDER BY created_at DESC LIMIT $1
                """,
                CONSECUTIVE_LOOKBACK,
            )
            strategy_rows = await conn.fetch(
                """
                SELECT strategy, outcome_label FROM signals
                WHERE outcome_label IN ('TARGET_HIT', 'STOP_HIT') AND NOT COALESCE(suppressed, false)
                ORDER BY created_at DESC LIMIT 500
                """,
            )
        except Exception as exc:
            return {"available": False, "reason": f"consecutive-losses query failed: {exc}"}

    overall_streak = _streak([r["outcome_label"] for r in overall_rows])

    by_strategy: dict[str, list[str]] = {}
    for r in strategy_rows:
        by_strategy.setdefault(r["strategy"], []).append(r["outcome_label"])
    strategy_streaks = {
        strategy: _streak(labels[:CONSECUTIVE_LOOKBACK]) for strategy, labels in by_strategy.items()
    }

    return {
        "available": True,
        "overall_consecutive_losses": overall_streak,
        "by_strategy": strategy_streaks,
        "lookback": CONSECUTIVE_LOOKBACK,
    }
