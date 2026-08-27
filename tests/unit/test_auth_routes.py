"""Unit tests for api.routes.auth's Upstox token validation/storage --
"Telegram Redesign & Token Modal" sprint (2026-08-27). Covers every
rejection path (malformed JSON, missing token, unreadable JWT, expired
JWT, live-check rejection) and the success path, plus the live-check's
own soft-fail-open behavior when Upstox itself is unreachable.

Real request/response cycle via aiohttp.test_utils, matching
test_api_middleware.py's own established pattern for this codebase --
`_verify_token_live` is monkeypatched at the module level rather than
mocking aiohttp itself, since what's under test here is the route's own
validation ORDER and response shape, not httpx/aiohttp plumbing.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import api.routes.auth as auth_module
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from api.routes.auth import routes as auth_routes


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})


def _make_app() -> tuple[web.Application, _FakeRedis]:
    app = web.Application()
    app.router.add_routes(auth_routes)
    redis = _FakeRedis()
    app["redis"] = redis
    return app, redis


def _make_jwt(exp: int) -> str:
    """A structurally-real JWT: three base64url segments. _jwt_expiry
    only ever reads segment[1]'s "exp" claim -- header/signature content
    is irrelevant to what's under test, so both are arbitrary bytes."""

    def _seg(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{_seg({'alg': 'HS256'})}.{_seg({'exp': exp})}.fake-signature"


async def test_missing_body_json_is_rejected() -> None:
    app, _ = _make_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/auth/upstox/token", data="not json")
        assert resp.status == 400
        body = await resp.json()
    assert body["status"] == "error"
    assert body["ok"] is False


async def test_empty_access_token_is_rejected() -> None:
    app, _ = _make_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/auth/upstox/token", json={"access_token": "  "})
        assert resp.status == 400
        body = await resp.json()
    assert "required" in body["message"].lower()


async def test_unparseable_jwt_is_rejected() -> None:
    app, _ = _make_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/auth/upstox/token", json={"access_token": "not-a-real-jwt"}
        )
        assert resp.status == 400
        body = await resp.json()
    assert "expiry" in body["message"].lower()


async def test_already_expired_jwt_is_rejected() -> None:
    app, _ = _make_app()
    token = _make_jwt(exp=int(time.time()) - 3600)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/auth/upstox/token", json={"access_token": token})
        assert resp.status == 400
        body = await resp.json()
    assert "already expired" in body["message"].lower()


async def test_live_check_rejection_returns_the_literal_error_message(monkeypatch: Any) -> None:
    """Real, if server-revoked, tokens: well-formed and not yet expired
    per their own JWT claim, but Upstox's own /v2/user/profile says no."""

    async def fake_live_check(token: str) -> tuple[bool, str]:
        return False, "Upstox rejected this token (401/403 from /v2/user/profile)."

    monkeypatch.setattr(auth_module, "_verify_token_live", fake_live_check)

    app, redis = _make_app()
    token = _make_jwt(exp=int(time.time()) + 3600)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/auth/upstox/token", json={"access_token": token})
        assert resp.status == 400
        body = await resp.json()
    assert body["status"] == "error"
    assert body["message"] == "Invalid Upstox Token"
    # Never stored -- a rejected token must not overwrite a real one.
    assert auth_module.KEY_AUTH_UPSTOX not in redis.store


async def test_valid_token_is_stored_and_returns_success(monkeypatch: Any) -> None:
    async def fake_live_check(token: str) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr(auth_module, "_verify_token_live", fake_live_check)

    app, redis = _make_app()
    exp = int(time.time()) + 3600
    token = _make_jwt(exp=exp)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/auth/upstox/token", json={"access_token": token})
        assert resp.status == 200
        body = await resp.json()

    assert body["ok"] is True
    assert body["status"] == "success"
    stored = json.loads(redis.store[auth_module.KEY_AUTH_UPSTOX])
    assert stored["access_token"] == token
    assert stored["expiry_ts"] == exp
    assert auth_module.KEY_FORCE_RECHECK in redis.store


async def test_live_check_soft_fails_open_when_upstox_is_unreachable(monkeypatch: Any) -> None:
    """A network hiccup reaching Upstox is not evidence the TOKEN is
    bad -- see _verify_token_live's own docstring. The route must still
    accept a structurally valid, unexpired token in that case."""

    class _RaisingSession:
        async def __aenter__(self) -> _RaisingSession:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("simulated network failure")

    monkeypatch.setattr(auth_module.aiohttp, "ClientSession", lambda: _RaisingSession())

    is_valid, reason = await auth_module._verify_token_live("irrelevant-token")
    assert is_valid is True
    assert reason == ""
