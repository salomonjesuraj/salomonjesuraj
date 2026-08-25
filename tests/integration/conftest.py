"""Shared fixtures for the Docker-backed integration suite.

Deliberate scope, disclosed here rather than silently: these tests exercise
the REAL running stack (the same `docker compose up -d` deployment every
phase of this session's own work was verified against) over real
connections -- Redis, Postgres, the api container's HTTP surface, and the
dashboard's nginx proxy. They do NOT stand up an isolated/ephemeral stack,
seed a synthetic symbol universe, or inject synthetic ticks through
ingestion -- that's a materially larger, separate project (mocking
Upstox's protobuf feed end to end) than "run the existing stack and assert
real contracts against it", which is what's implemented here.

Given that, every test in this package requires the stack to actually be
up (`docker compose up -d` from the repo root). Rather than a scary,
unexplained connection-refused failure, the fixtures below check
reachability once per session and SKIP (not fail) the whole suite with an
actionable message if it isn't -- the standard, CI-friendly shape for an
integration suite that has a real external dependency.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import aiohttp
import asyncpg
import pytest
import redis.asyncio as aioredis

REDIS_URL = "redis://localhost:6379/0"
POSTGRES_DSN = "postgresql://infusion:changeme@localhost:5432/infusion"
API_BASE_URL = "http://localhost:8000"
DASHBOARD_BASE_URL = "http://localhost:3000"

_STACK_SKIP_REASON = (
    "Integration suite requires the real stack running -- "
    "start it with `docker compose up -d` from the repo root, then re-run "
    "`pytest tests/integration/` (or `make test-integration`)."
)


def _stack_is_up() -> bool:
    """One-shot, synchronous-enough reachability probe so every test file's
    fixtures can share a single skip decision instead of each re-deriving
    it (and each producing its own confusing timeout)."""

    async def _check() -> bool:
        try:
            client = aioredis.from_url(REDIS_URL, socket_connect_timeout=2)
            try:
                await client.ping()
            finally:
                await client.aclose()
        except Exception:
            return False
        try:
            conn = await asyncio.wait_for(asyncpg.connect(POSTGRES_DSN), timeout=2)
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
        except Exception:
            return False
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{API_BASE_URL}/api/diagnostics") as resp:
                    if resp.status != 200:
                        return False
                async with session.get(DASHBOARD_BASE_URL) as resp:
                    if resp.status != 200:
                        return False
        except Exception:
            return False
        return True

    return asyncio.run(_check())


@pytest.fixture(scope="session", autouse=True)
def _require_stack() -> None:
    if not _stack_is_up():
        pytest.skip(_STACK_SKIP_REASON, allow_module_level=True)


@pytest.fixture
async def redis_client() -> AsyncGenerator[aioredis.Redis, None]:
    """Real Redis client, decode_responses=False -- matches every
    service's own client config (main.py's `Redis.from_url(..., decode_
    responses=False)`), so tests decode bytes exactly the way production
    code does rather than getting an easier, unrepresentative str client."""
    client = aioredis.from_url(REDIS_URL, decode_responses=False)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def pg_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def api_client() -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Real HTTP client against the live api container (localhost:8000,
    per docker-compose.yml's own port mapping) -- these tests exercise
    the actual deployed service, not an in-process aiohttp test client
    against route handlers imported directly."""
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(base_url=API_BASE_URL, timeout=timeout) as session:
        yield session


@pytest.fixture
async def dashboard_client() -> AsyncGenerator[aiohttp.ClientSession, None]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(base_url=DASHBOARD_BASE_URL, timeout=timeout) as session:
        yield session
