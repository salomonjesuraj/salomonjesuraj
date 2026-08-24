"""EBIE EB-15 Phase 6 item 11 -- the deferred 30/45/60-minute ATR label
study.

Real, load-bearing finding from EB-10 (earlier this session), directly
relevant here: archiver/config.py's signal_ttl_min was 5 minutes until
EB-10 widened it to 75 (tracker_lookback_min 60->90), because EVERY
signal archived before that point could only ever resolve within 0-5.5
minutes of firing -- meaning NONE of them can honestly answer "what
would have happened by minute 30/45/60," regardless of how many
thousands of such rows exist in the archive. This module therefore only
ever considers signals created AT OR AFTER the EB-10 deploy
(EB10_TTL_WIDEN_AT, commit d7d5001) -- the first point in this system's
history where a genuine 90-minute observation window existed at all.

Deliberately does NOT reconstruct historical OHLC price paths from
scratch. archiver/tracker.py's own 30s polling loop already recorded,
for every tracked signal, the real time_to_target_min/time_to_stop_min
(precise minute-offsets from firing to each real outcome, or NULL if
that outcome never happened within the tracker's own real lookback
window) -- classify_at_window() below derives "what would this signal's
label have been at 30/45/60 minutes" directly from those two already-
archived numbers, which is exactly equivalent to a full price-path
replay for this purpose (target-before-stop is unambiguous from two
timestamps) without needing to re-fetch or re-derive anything.

Required outcome vocabulary per the directive: target hit, stop hit,
timeout, trap, invalidated. Mapped as: TARGET_HIT and STOP_HIT
(=invalidated -- a stop-hit IS the invalidation level being crossed) are
the two per-window outcomes; TIMEOUT is the third (neither happened
within that window); TRAP is not a fourth mutually-exclusive per-window
bucket but a real, reused sub-classification of the signal's OWN FINAL
outcome (api/trap_labels.py's already-built, already-validated
classify_false_break() -- a STOP_HIT within 15 minutes of firing), kept
as a distinct dimension rather than invented a second time with a
different, unvalidated definition.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from api.trap_labels import FALSE_BREAK_FAST_STOP_MIN, classify_false_break

LABEL_STUDY_WINDOWS_MIN = (30, 45, 60)

# The exact moment archiver/config.py's signal_ttl_min widened 5->75 and
# tracker_lookback_min widened 60->90 (commit d7d5001, EB-10). Any signal
# created before this point was NEVER capable of resolving past ~5.5
# minutes, regardless of its own recorded numbers -- excluded outright,
# not included-and-hoped-honest.
EB10_TTL_WIDEN_AT_ISO = "2026-08-20T08:37:19+00:00"  # 2026-08-20 14:07:19+05:30, in UTC
# asyncpg needs a real datetime for a $N::timestamptz parameter -- a bare
# string caused a real, live-caught bug here (asyncpg's own type
# inference happens before the SQL-side cast runs), fixed by parsing
# once at import time rather than passing the ISO string straight through.
EB10_TTL_WIDEN_AT = datetime.fromisoformat(EB10_TTL_WIDEN_AT_ISO).astimezone(UTC)

# The directive's own minimum sample before a recommendation should be
# trusted (matching item 10's own "300 episodes" scale of evidence, not
# a smaller ad hoc number invented just for this study).
MIN_SAMPLE_FOR_RECOMMENDATION = 100


def classify_at_window(
    time_to_target_min: float | None, time_to_stop_min: float | None, window_min: int
) -> str:
    """TARGET_HIT / STOP_HIT / TIMEOUT at a given window, from the
    signal's own already-archived real timing -- see this module's own
    docstring for why this is equivalent to a real price-path replay for
    this specific question. If both would resolve within the window
    (a real, if unusual, case -- e.g. a wide initial wick), whichever
    happened first wins, exactly matching how a live price path can only
    ever cross one level first."""
    target_in = time_to_target_min is not None and time_to_target_min <= window_min
    stop_in = time_to_stop_min is not None and time_to_stop_min <= window_min
    if target_in and stop_in:
        assert time_to_target_min is not None
        assert time_to_stop_min is not None
        return "TARGET_HIT" if time_to_target_min <= time_to_stop_min else "STOP_HIT"
    if target_in:
        return "TARGET_HIT"
    if stop_in:
        return "STOP_HIT"
    return "TIMEOUT"


Row = dict[str, Any]
Result = dict[str, Any]


def _window_breakdown(rows: list[Row], window_min: int) -> Result:
    counts = {"TARGET_HIT": 0, "STOP_HIT": 0, "TIMEOUT": 0}
    for r in rows:
        label = classify_at_window(
            r.get("time_to_target_min"), r.get("time_to_stop_min"), window_min
        )
        counts[label] += 1
    total = len(rows)
    return {
        "window_min": window_min,
        "n": total,
        "target_hit": counts["TARGET_HIT"],
        "stop_hit_invalidated": counts["STOP_HIT"],
        "timeout": counts["TIMEOUT"],
        "target_hit_pct": round(100 * counts["TARGET_HIT"] / total, 1) if total else None,
        "stop_hit_pct": round(100 * counts["STOP_HIT"] / total, 1) if total else None,
        "timeout_pct": round(100 * counts["TIMEOUT"] / total, 1) if total else None,
    }


async def compute_label_study(pool: Any) -> Result:
    """Real, on-demand report -- computed fresh each call (same
    "checked occasionally, not polled" shape as shadow_validation.py's
    own report), not a scheduled job. Honestly reports whatever real
    sample currently exists (expected to be very small immediately
    after EB-10's TTL widen), and refuses to name a "recommended"
    window below MIN_SAMPLE_FOR_RECOMMENDATION rather than guessing
    from noise.
    """
    if not pool:
        return {"available": False, "reason": "Postgres analytics pool is not available."}

    try:
        async with pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT symbol, strategy, outcome_label, time_to_target_min, time_to_stop_min,
                       created_at
                FROM signals
                WHERE created_at >= $1::timestamptz
                  AND outcome_label IN ('TARGET_HIT', 'STOP_HIT', 'EXPIRED')
                  AND NOT COALESCE(suppressed, false)
                ORDER BY created_at DESC
                """,
                EB10_TTL_WIDEN_AT,
            )
    except Exception as exc:
        return {"available": False, "reason": f"label-study query failed: {exc}"}

    rows: list[Row] = [dict(r) for r in records]
    total = len(rows)
    sessions = {r["created_at"].date() for r in rows if r.get("created_at")}

    windows = [_window_breakdown(rows, w) for w in LABEL_STUDY_WINDOWS_MIN]

    # Trap sub-classification -- reuses EB-9's own already-validated
    # definition against each signal's REAL final outcome (not
    # window-scoped -- a fast reversal is a property of the actual
    # resolution, independent of which study window you're asking about).
    trap_flags = [
        classify_false_break(r.get("outcome_label"), r.get("time_to_stop_min")) for r in rows
    ]
    trap_labeled = [t for t in trap_flags if t is not None]
    trap_count = sum(1 for t in trap_labeled if t)

    # Compare against "current signal outcome logic" -- the signal's own
    # REAL final outcome_label, unconstrained by any of the 30/45/60
    # study windows (this is exactly today's production label).
    current_logic_counts = {"TARGET_HIT": 0, "STOP_HIT": 0, "EXPIRED": 0}
    for r in rows:
        label = r.get("outcome_label")
        if label in current_logic_counts:
            current_logic_counts[label] += 1

    recommendation = None
    recommendation_reason = (
        f"Need at least {MIN_SAMPLE_FOR_RECOMMENDATION} real post-EB-10 decided signals "
        f"before recommending a production label window -- {total} available. "
        "Re-check this report as real signals accumulate."
    )
    if total >= MIN_SAMPLE_FOR_RECOMMENDATION:
        # A real methodology, not a placeholder: prefer the SHORTEST
        # window whose timeout rate is not meaningfully worse than the
        # longest window's (diminishing returns from waiting longer),
        # i.e. most of the "real" resolutions already happened by then.
        longest = windows[-1]
        longest_timeout = longest["timeout_pct"]
        for w in windows:
            window_timeout = w["timeout_pct"]
            if longest_timeout is None or window_timeout is None:
                continue
            if window_timeout <= longest_timeout + 10.0:
                recommendation = w["window_min"]
                break
        recommendation_reason = (
            f"Shortest window ({recommendation} min) whose timeout rate "
            f"({next(w['timeout_pct'] for w in windows if w['window_min'] == recommendation)}%) "
            f"is within 10 points of the {longest['window_min']}-min window's "
            f"({longest['timeout_pct']}%) -- real evidence from {total} decided signals."
        )

    return {
        "available": True,
        # .isoformat() -- aiohttp's default JSON encoder doesn't handle a
        # raw datetime object (the same class of bug this session's own
        # EB-12 commit already found once, there for a Decimal instead).
        "eb10_ttl_widen_at": EB10_TTL_WIDEN_AT.isoformat(),
        "total_decided_signals_since_eb10": total,
        "session_count": len(sessions),
        "windows_min": list(LABEL_STUDY_WINDOWS_MIN),
        "window_breakdown": windows,
        "current_production_logic": {
            "target_hit": current_logic_counts["TARGET_HIT"],
            "stop_hit_invalidated": current_logic_counts["STOP_HIT"],
            "expired_timeout": current_logic_counts["EXPIRED"],
            "note": "The signal's own real final outcome_label -- today's actual production logic, unconstrained by any study window.",
        },
        "trap": {
            "n_labeled": len(trap_labeled),
            "trap_count": trap_count,
            "trap_rate_pct": round(100 * trap_count / len(trap_labeled), 1)
            if trap_labeled
            else None,
            "definition": f"STOP_HIT within {FALSE_BREAK_FAST_STOP_MIN} minutes of firing (reused from EB-9's own already-validated false-break label, not re-derived).",
        },
        "recommended_window_min": recommendation,
        "recommendation_reason": recommendation_reason,
        "min_sample_for_recommendation": MIN_SAMPLE_FOR_RECOMMENDATION,
    }
