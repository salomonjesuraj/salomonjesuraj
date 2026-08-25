"""Schema-contract tests for the `signals` table.

Real regression-catcher, not a placeholder: every column asserted here is
one this session's own code (api/routes/ebie_candidates.py,
api/routes/backtest.py, api/ml_classifier.py, api/ai_query.py) directly
SELECTs by name. A migration that renamed or dropped one of these columns
wouldn't show up as a Python type error (asyncpg errors are only raised at
query time, not import time) -- this test exists to catch exactly that
class of regression before it reaches a live request. Column names/types
cross-checked directly against migrations/init.sql and 002/003/005, not
guessed from memory.
"""

from __future__ import annotations

# name -> expected Postgres data_type (as information_schema.columns
# reports it), covering every column ebie_candidates.py, backtest.py,
# ml_classifier.py, and ai_query.py select from `signals` by name.
REQUIRED_SIGNALS_COLUMNS: dict[str, str] = {
    # init.sql (base table)
    "created_at": "timestamp with time zone",
    "symbol": "text",
    "strategy": "text",
    "signal_type": "text",
    "conviction_score": "numeric",
    "conviction_grade": "text",
    "features": "jsonb",
    "outcome_label": "text",
    # 002_phase4_outcome_tracking.sql
    "signal_id": "uuid",
    "entry_price": "numeric",
    "invalidation_price": "numeric",
    "target_price": "numeric",
    "risk_reward_ratio": "numeric",
    "sector_id": "text",
    "market_regime": "text",
    "pre_breakout_state": "text",
    "suppressed": "boolean",
    "suppression_reason": "text",
    "sub_scores": "jsonb",
    "target_hit_at": "timestamp with time zone",
    "stop_hit_at": "timestamp with time zone",
    "session_hour": "text",
    "time_to_target_min": "numeric",
    "time_to_stop_min": "numeric",
    # 005_option_premium_capture.sql
    "entry_premium_ask": "numeric",
    "entry_premium_bid": "numeric",
    "exit_premium_bid": "numeric",
}


async def test_signals_table_has_every_column_this_sessions_code_depends_on(pg_conn) -> None:
    rows = await pg_conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'signals'"
    )
    live_columns = {r["column_name"]: r["data_type"] for r in rows}

    missing = sorted(set(REQUIRED_SIGNALS_COLUMNS) - set(live_columns))
    assert not missing, f"signals table is missing column(s) real code depends on: {missing}"

    mismatched = {
        name: (expected, live_columns[name])
        for name, expected in REQUIRED_SIGNALS_COLUMNS.items()
        if live_columns[name] != expected
    }
    assert not mismatched, f"signals column type drift (expected -> actual): {mismatched}"


async def test_ebie_state_transitions_has_the_columns_the_dashboard_reads(pg_conn) -> None:
    """api/routes/ebie_state.py's /api/ebie/transitions/recent and
    /api/ebie/comparison -- both surfaced in the EBIE Verdict panel's
    Event Timeline and drift-monitoring strip this session built."""
    rows = await pg_conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ebie_state_transitions'"
    )
    live_columns = {r["column_name"] for r in rows}
    required = {
        "symbol",
        "direction",
        "sector_id",
        "state",
        "prev_state",
        "reason",
        "legacy_tier",
        "legacy_pb_state",
        "score",
        "ltp",
        "transitioned_at",
    }
    missing = sorted(required - live_columns)
    assert not missing, f"ebie_state_transitions is missing column(s): {missing}"
