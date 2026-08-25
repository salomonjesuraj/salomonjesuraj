"""Unit tests for api.middleware.redis_error_middleware.

Covers the graceful-degradation fix for the live-caught issue (2026-08-25):
an uncaught redis.exceptions.RedisError used to propagate all the way to
aiohttp's own generic handler, which returns a plain text/HTML 500 page --
not JSON -- breaking every dashboard fetch().then(r => r.json()) caller on
top of the real Redis hiccup. Real requests are exercised through an actual
aiohttp.test_utils server rather than calling the middleware function
directly, since the behavior being verified is the full request/response
cycle (status code + JSON body), not just the coroutine's return value.
"""

from __future__ import annotations

import redis.exceptions
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from api.middleware import redis_error_middleware


def _make_app(handler) -> web.Application:
    app = web.Application(middlewares=[redis_error_middleware])
    app.router.add_get("/probe", handler)
    return app


async def test_redis_timeout_degrades_to_a_structured_503() -> None:
    async def handler(request: web.Request) -> web.Response:
        raise redis.exceptions.TimeoutError("Timeout reading from redis:6379")

    async with TestClient(TestServer(_make_app(handler))) as client:
        resp = await client.get("/probe")
        assert resp.status == 503
        body = await resp.json()

    assert body["available"] is False
    assert body["error"] == "temporarily_unavailable"
    assert body["source"] == "redis"
    assert "TimeoutError" in body["reason"]


async def test_redis_connection_error_also_degrades() -> None:
    """Catches the RedisError base class, not just TimeoutError --
    ConnectionError/BusyLoadingError are the same transient-infra class
    of failure this fix targets."""

    async def handler(request: web.Request) -> web.Response:
        raise redis.exceptions.ConnectionError("Error 111 connecting to redis:6379")

    async with TestClient(TestServer(_make_app(handler))) as client:
        resp = await client.get("/probe")
        assert resp.status == 503
        body = await resp.json()

    assert body["available"] is False
    assert body["source"] == "redis"


async def test_successful_response_passes_through_unchanged() -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"available": True, "value": 42})

    async with TestClient(TestServer(_make_app(handler))) as client:
        resp = await client.get("/probe")
        assert resp.status == 200
        body = await resp.json()

    assert body == {"available": True, "value": 42}


async def test_non_redis_exception_is_not_swallowed() -> None:
    """Scope check: a route bug (or any other exception class) must keep
    surfacing as a real error -- this middleware only degrades genuine
    Redis transport failures, not arbitrary handler bugs."""

    async def handler(request: web.Request) -> web.Response:
        raise ValueError("not a redis problem")

    async with TestClient(TestServer(_make_app(handler))) as client:
        resp = await client.get("/probe")
        # aiohttp's own generic handler takes over for anything the
        # middleware doesn't catch -- still a 500, but that's the correct,
        # unmodified behavior for a real handler bug.
        assert resp.status == 500


async def test_a_route_that_already_catches_redis_error_is_unaffected() -> None:
    """Several routes (routes/backtest.py, routes/ebie_state.py, etc.)
    already catch RedisError themselves and return their own
    {"available": False, "reason": ...} shape -- the middleware must never
    see or alter that, since the exception never escapes the handler."""

    async def handler(request: web.Request) -> web.Response:
        try:
            raise redis.exceptions.TimeoutError("boom")
        except redis.exceptions.RedisError:
            return web.json_response({"available": False, "reason": "Redis not available."})

    async with TestClient(TestServer(_make_app(handler))) as client:
        resp = await client.get("/probe")
        assert resp.status == 200
        body = await resp.json()

    assert body == {"available": False, "reason": "Redis not available."}
