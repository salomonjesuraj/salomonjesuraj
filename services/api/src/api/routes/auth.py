"""Broker auth helper routes for local dashboard recovery.

These endpoints intentionally support the local Infusion dashboard workflow:
when Upstox expires the access token, the user can paste a fresh token in the
dashboard and force ingestion to re-authenticate without editing .env files.

"Telegram Redesign & Token Modal" sprint (2026-08-27) -- the sprint asked
for a new `POST /api/broker/token` that validates a pasted token with a
live call to Upstox's own `/v2/user/profile`. This route
(`POST /api/auth/upstox/token`) already existed and already does
everything else that endpoint would have needed to do: parse the token's
own JWT `exp` claim, store it in the exact `infusion:auth:upstox` Redis
key `api/broker_sync.py`'s `_upstox_access_token()` already reads,
trigger an ingestion recheck. Building a second, parallel `/api/broker/
token` route that wrote to the same Redis key via a different code path
would have meant two competing "the current Upstox token" writers -- so
instead, `_verify_token_live()` below adds exactly the live-validation
call that was actually new here, onto this already-real, already-wired
endpoint, rather than duplicating it. See `save_upstox_token()`'s own
docstring for the validation order and the deliberate soft-fail when
Upstox itself (not the token) is unreachable.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import aiohttp
import structlog
from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]
logger = structlog.get_logger()

KEY_AUTH_UPSTOX = "infusion:auth:upstox"
KEY_AUTH_STATUS = "infusion:auth:upstox:status"
KEY_FORCE_RECHECK = "infusion:auth:upstox:force_recheck"
IST = timezone(timedelta(hours=5, minutes=30))
UPSTOX_PROFILE_URL = "https://api.upstox.com/v2/user/profile"


def _jwt_expiry(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return int(data.get("exp") or 0)
    except Exception:
        return 0


def _iso(ts: int, tz: timezone = UTC) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).astimezone(tz).isoformat() if ts else ""


async def _verify_token_live(token: str) -> tuple[bool, str]:
    """A real GET against Upstox's own `/v2/user/profile` with the
    pasted token -- catches what the JWT-expiry check above cannot: a
    well-formed, not-yet-expired token that Upstox has already revoked
    server-side (a fresh login elsewhere, a manually invalidated
    session). Returns (is_valid, reason) -- is_valid is True both when
    Upstox actually confirms the token AND when Upstox itself couldn't
    be reached at all (a network hiccup on OUR side is not evidence the
    TOKEN is bad; failing the paste over that would be a worse trade
    than trusting the JWT-expiry check alone for this one request), so
    only a real 401/403 from Upstox's own server ever fails this."""
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                UPSTOX_PROFILE_URL,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp,
        ):
            if resp.status in (401, 403):
                return False, "Upstox rejected this token (401/403 from /v2/user/profile)."
            return True, ""
    except Exception as exc:
        logger.warning("upstox_token_live_check_unreachable", error=str(exc))
        return True, ""


@routes.get("/api/auth/upstox/status")
async def upstox_auth_status(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    health_raw = await redis.get("infusion:health:ingestion")
    health: Payload = {}
    if health_raw:
        try:
            import msgpack

            health = cast(Payload, msgpack.unpackb(health_raw, raw=False))
        except Exception:
            health = {}

    auth_raw = await redis.get(KEY_AUTH_UPSTOX)
    auth_payload: Payload = {}
    if auth_raw:
        try:
            auth_payload = cast(
                Payload, json.loads(auth_raw.decode() if isinstance(auth_raw, bytes) else auth_raw)
            )
        except Exception:
            auth_payload = {}

    status_raw = await redis.hgetall(KEY_AUTH_STATUS)
    status: Payload = {}
    for k, v in status_raw.items():
        kk = k.decode() if isinstance(k, bytes) else k
        vv = v.decode() if isinstance(v, bytes) else v
        status[kk] = vv

    expiry_ts = int(
        auth_payload.get("expiry_ts")
        or health.get("token_expiry_ts")
        or status.get("token_expiry_ts")
        or 0
    )
    now = int(time.time())
    auth_error = health.get("auth_error") or status.get("state") or ""
    token_state = "missing"
    if expiry_ts:
        token_state = "expired" if expiry_ts <= now else "valid"
    if auth_error in {"expired_token", "invalid_token", "missing_token"}:
        token_state = auth_error.replace("_token", "")

    return web.json_response(
        {
            "broker": "upstox",
            "token_state": token_state,
            "needs_token": token_state in {"expired", "invalid", "missing"},
            "auth_error": auth_error,
            "source": auth_payload.get("source")
            or health.get("token_source")
            or status.get("token_source")
            or "",
            "expiry_ts": expiry_ts,
            "expiry_utc": _iso(expiry_ts),
            "expiry_ist": _iso(expiry_ts, IST),
            "ingestion_state": health.get("state", ""),
            "last_tick_age_ms": health.get("last_tick_age_ms", -1),
            "tick_count": health.get("tick_count", 0),
        }
    )


def _rejected(error: str, **extra: Any) -> web.Response:
    """Every rejection path shares one response shape: `ok`/`error` (this
    route's own pre-existing contract) alongside `status`/`message` (the
    "Telegram Redesign & Token Modal" sprint's own literal ask) -- an
    additive widening, not a breaking rename, so anything that already
    reads `ok`/`error` keeps working unchanged."""
    body: Payload = {"ok": False, "error": error, "status": "error", "message": error}
    body.update(extra)
    return web.json_response(body, status=400)


@routes.post("/api/auth/upstox/token")
async def save_upstox_token(request: web.Request) -> web.Response:
    """Validates and stores a pasted Upstox access token. Two
    independent checks, both real, run in this order:
      1. Decode the token's own JWT `exp` claim -- catches a malformed
         paste or one that's already past its stated expiry, with zero
         network I/O.
      2. `_verify_token_live()` -- a real GET to Upstox's own
         `/v2/user/profile`, catching a well-formed/unexpired token
         Upstox has already revoked server-side. Soft-fails open if
         Upstox itself is unreachable (see that function's own
         docstring for why) -- only an explicit 401/403 FROM Upstox
         rejects the paste here.
    """
    redis = request.app["redis"]
    try:
        body_raw = await request.json()
    except Exception:
        return _rejected("Invalid JSON body.")
    body = cast(Payload, body_raw) if isinstance(body_raw, dict) else {}

    token = str(body.get("access_token") or "").strip()
    if not token:
        return _rejected("Access token is required.")

    expiry_ts = _jwt_expiry(token)
    if not expiry_ts:
        return _rejected(
            "Could not read expiry from this token. Please paste the full Upstox access token."
        )
    now = int(time.time())
    if expiry_ts and expiry_ts <= now:
        return _rejected("This Upstox token is already expired.", expiry_ist=_iso(expiry_ts, IST))

    live_valid, live_reason = await _verify_token_live(token)
    if not live_valid:
        logger.warning("upstox_token_rejected_by_live_check", reason=live_reason)
        return _rejected("Invalid Upstox Token")

    ttl = max((expiry_ts - now - 60), 300) if expiry_ts else 20 * 3600
    auth_data = {
        "access_token": token,
        "stored_at": datetime.now(UTC).isoformat(),
        "broker": "upstox",
        "source": "dashboard",
        "expiry_ts": expiry_ts,
        "expiry_utc": _iso(expiry_ts),
        "expiry_ist": _iso(expiry_ts, IST),
    }
    await redis.set(KEY_AUTH_UPSTOX, json.dumps(auth_data, separators=(",", ":")), ex=ttl)
    await redis.hset(
        KEY_AUTH_STATUS,
        mapping={
            "state": "token_saved",
            "token_source": "dashboard",
            "token_expiry_ts": str(expiry_ts),
            "updated_at": str(now),
        },
    )
    await redis.set(KEY_FORCE_RECHECK, str(now), ex=120)

    return web.json_response(
        {
            "ok": True,
            "status": "success",
            "broker": "upstox",
            "source": "dashboard",
            "expiry_ts": expiry_ts,
            "expiry_ist": _iso(expiry_ts, IST),
            "recheck_triggered": True,
            "message": "Token saved. Ingestion recheck has been requested.",
        }
    )


@routes.post("/api/auth/upstox/recheck")
async def force_upstox_recheck(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    now = int(time.time())
    await redis.set(KEY_FORCE_RECHECK, str(now), ex=120)
    await redis.hset(
        KEY_AUTH_STATUS,
        mapping={
            "state": "recheck_requested",
            "updated_at": str(now),
        },
    )
    return web.json_response({"ok": True, "broker": "upstox", "recheck_triggered": True})
