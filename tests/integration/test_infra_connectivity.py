"""Real connectivity checks against the running stack's own infrastructure.

Deliberately minimal -- these exist to give a fast, unambiguous first
failure ("Redis/Postgres itself is unreachable") before any of the
higher-level contract tests run, rather than every later test failing with
its own confusing connection error.
"""

from __future__ import annotations


async def test_redis_responds_to_ping(redis_client) -> None:
    assert await redis_client.ping() is True


async def test_postgres_responds_to_a_real_query(pg_conn) -> None:
    assert await pg_conn.fetchval("SELECT 1") == 1


async def test_postgres_signals_table_exists(pg_conn) -> None:
    exists = await pg_conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'signals')"
    )
    assert exists is True


async def test_postgres_ebie_state_transitions_table_exists(pg_conn) -> None:
    """EB-1's own canonical-state table -- this session's entire EBIE
    Verdict panel (event timeline, drift-monitoring) reads from it."""
    exists = await pg_conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'ebie_state_transitions')"
    )
    assert exists is True
