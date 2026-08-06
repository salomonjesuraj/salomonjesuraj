"""Upstox OAuth callback server for Infusion.

Flow:
  1. User opens /auth/login
  2. Upstox redirects to /callback/upstox?code=...
  3. We exchange code for access token
  4. Token is stored in Redis at infusion:auth:upstox
  5. Ingestion reads the token and connects to Upstox Market Data Feed V3
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp
import structlog
from aiohttp import web
from redis.asyncio import Redis

logger = structlog.get_logger()

KEY_AUTH_UPSTOX = "infusion:auth:upstox"


def _jwt_expiry(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return int(data.get("exp") or 0)
    except Exception:
        return 0


async def start_oauth_server(redis: Redis, settings) -> None:
    """Start the OAuth callback HTTP server."""
    app = web.Application()
    app["redis"] = redis
    app["settings"] = settings

    app.router.add_get("/callback/upstox", handle_upstox_callback)
    app.router.add_get("/auth/status", handle_auth_status)
    app.router.add_get("/auth/login-urls", handle_login_urls)
    app.router.add_get("/auth/login", handle_login_page)
    app.router.add_get("/auth/success", handle_success_page)
    app.router.add_get("/", handle_login_page)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(
        runner,
        settings.oauth_callback_host,
        settings.oauth_callback_port,
    )
    await site.start()
    logger.info(
        "oauth_server_started",
        broker="upstox",
        host=settings.oauth_callback_host,
        port=settings.oauth_callback_port,
    )

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await runner.cleanup()
        logger.info("oauth_server_stopped")


async def _upstox_credentials(request: web.Request) -> tuple[str, str, str]:
    settings = request.app["settings"]
    redis: Redis = request.app["redis"]

    api_key = getattr(settings, "upstox_api_key", "") or (
        await redis.get("infusion:config:upstox_api_key") or b""
    ).decode()
    api_secret = getattr(settings, "upstox_api_secret", "") or (
        await redis.get("infusion:config:upstox_api_secret") or b""
    ).decode()
    redirect_uri = getattr(settings, "upstox_redirect_uri", "") or (
        await redis.get("infusion:config:upstox_redirect_uri") or b""
    ).decode()
    return api_key, api_secret, redirect_uri


async def _upstox_login_url(request: web.Request) -> str:
    api_key, _, redirect_uri = await _upstox_credentials(request)
    if not api_key or not redirect_uri:
        return ""
    return (
        "https://api.upstox.com/v2/login/authorization/dialog"
        f"?client_id={quote(api_key)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        "&response_type=code"
    )


async def handle_upstox_callback(request: web.Request) -> web.Response:
    """Exchange Upstox authorization code for an access token."""
    redis: Redis = request.app["redis"]
    auth_code = request.query.get("code", "")
    if not auth_code:
        return web.Response(
            text="Upstox login failed. No auth code received.",
            content_type="text/html",
            status=400,
        )

    api_key, api_secret, redirect_uri = await _upstox_credentials(request)
    if not api_key or not api_secret or not redirect_uri:
        return web.Response(
            text="Upstox API key/secret/redirect URI are not configured.",
            content_type="text/html",
            status=500,
        )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.upstox.com/v2/login/authorization/token",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "code": auth_code,
                    "client_id": api_key,
                    "client_secret": api_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.error("upstox_token_exchange_failed", status=resp.status, body=body[:200])
                    return web.Response(
                        text=f"Upstox token exchange failed. HTTP {resp.status}: {body[:300]}",
                        content_type="text/html",
                        status=500,
                    )
                data = json.loads(body)

        access_token = data.get("access_token", "")
        if not access_token:
            return web.Response(
                text="No access token in Upstox response.",
                content_type="text/html",
                status=500,
            )

        expiry_ts = _jwt_expiry(access_token)
        ttl = max(expiry_ts - int(datetime.now(timezone.utc).timestamp()) - 60, 300) if expiry_ts else 20 * 3600
        auth_data = json.dumps({
            "access_token": access_token,
            "login_time": datetime.utcnow().isoformat(),
            "broker": "upstox",
            "expiry_ts": expiry_ts,
            "expiry_utc": datetime.fromtimestamp(expiry_ts, tz=timezone.utc).isoformat() if expiry_ts else "",
        })
        await redis.set(KEY_AUTH_UPSTOX, auth_data)
        await redis.expire(KEY_AUTH_UPSTOX, ttl)
        await redis.hset("infusion:auth:upstox:status", mapping={
            "state": "authenticated",
            "token_source": "redis",
            "token_expiry_ts": str(expiry_ts),
            "updated_at": str(int(datetime.now(timezone.utc).timestamp())),
        })
        logger.info("upstox_token_stored", expiry_ts=expiry_ts, ttl=ttl)

        return web.HTTPFound("/auth/success")

    except Exception as exc:
        logger.error("upstox_callback_error", error=str(exc))
        return web.Response(text=f"Upstox login error: {exc}", content_type="text/html", status=500)


async def handle_auth_status(request: web.Request) -> web.Response:
    """Return current Upstox auth status."""
    redis: Redis = request.app["redis"]
    raw = await redis.get(KEY_AUTH_UPSTOX)
    ttl = await redis.ttl(KEY_AUTH_UPSTOX)
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw) if raw else {}
    return web.json_response({
        "broker": "upstox",
        "upstox": {
            "authenticated": raw is not None,
            "ttl_seconds": max(ttl, 0),
            "login_time": payload.get("login_time"),
            "expiry_ts": payload.get("expiry_ts"),
            "expiry_utc": payload.get("expiry_utc"),
        },
    })


async def handle_login_urls(request: web.Request) -> web.Response:
    """Return Upstox login URL."""
    return web.json_response({"upstox": await _upstox_login_url(request)})


async def handle_login_page(request: web.Request) -> web.Response:
    """Render Upstox login/status page."""
    redis: Redis = request.app["redis"]
    api_key, _, redirect_uri = await _upstox_credentials(request)
    login_url = await _upstox_login_url(request)
    raw = await redis.get(KEY_AUTH_UPSTOX)
    ttl = await redis.ttl(KEY_AUTH_UPSTOX)

    is_auth = raw is not None and ttl > 0
    login_time = ""
    if raw:
        try:
            payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            login_time = (payload.get("login_time", "")[:19]).replace("T", " ") + " UTC"
        except Exception:
            pass
    ttl_h = round(ttl / 3600, 1) if ttl > 0 else 0

    status_color = "#4ade80" if is_auth else "#f87171"
    status_bg = "#14532d" if is_auth else "#450a0a"
    status_text = (
        f"Authenticated with Upstox — expires in {ttl_h}h. Login time: {login_time}"
        if is_auth
        else "Not authenticated — login with Upstox below."
    )
    btn_style = "opacity:.4;pointer-events:none;cursor:not-allowed" if not login_url else ""
    masked_key = f"{api_key[:6]}..." if api_key else "not set"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Infusion — Upstox Login</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0 }}
    body {{ background: #080c16; color: #e2e8f0; font-family: Segoe UI, system-ui, sans-serif;
           min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
    .card {{ background: linear-gradient(145deg, #10182a, #0d1220); border: 1px solid #29334f;
             border-radius: 18px; padding: 40px 48px; max-width: 520px; width: 100%;
             box-shadow: 0 24px 80px rgba(0,0,0,.35); }}
    .logo {{ font-size: 32px; font-weight: 900; color: #7c3aed; letter-spacing: 2px; text-align:center }}
    .sub  {{ font-size: 13px; color: #94a3b8; margin: 6px 0 28px; text-align:center }}
    .status {{ padding: 13px 16px; border-radius: 10px; font-size: 13px;
               background: {status_bg}; color: {status_color}; margin-bottom: 24px; line-height: 1.5 }}
    .btn {{ display: block; text-align:center; padding: 15px 32px; background: #7c3aed;
            color: #fff; font-size: 15px; font-weight: 800; border-radius: 12px;
            text-decoration: none; transition: transform .15s, background .2s; {btn_style} }}
    .btn:hover {{ background: #6d28d9; transform: translateY(-1px) }}
    .steps {{ margin-top: 26px; text-align: left; font-size: 12px; color: #94a3b8; line-height: 1.85 }}
    .steps li {{ margin-left: 16px }}
    .meta {{ margin-top: 20px; font-size: 11px; color: #64748b; font-family: ui-monospace, monospace; line-height:1.7 }}
    .refresh {{ margin-top: 20px; display: inline-block; padding: 8px 18px;
                background: #1e293b; color: #cbd5e1; font-size: 12px; border-radius: 8px; text-decoration: none }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">INFUSION</div>
    <div class="sub">Options-first dashboard — Upstox live data only</div>
    <div class="status">{status_text}</div>
    {"<a class='btn' href='" + login_url + "'>Login with Upstox →</a>" if login_url else "<div style='color:#f87171;font-size:13px'>Upstox API key or redirect URI not configured</div>"}
    <ol class="steps">
      <li>Click <strong>Login with Upstox</strong>.</li>
      <li>Complete Upstox authentication.</li>
      <li>You will return to this callback server automatically.</li>
      <li>Restart or wait for ingestion reconnect; live ticks should resume shortly.</li>
    </ol>
    <div class="meta">API key: {masked_key}<br>Redirect URI: {redirect_uri or "not set"}</div>
    <a class="refresh" href="/auth/login">Refresh status</a>
  </div>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


async def handle_success_page(request: web.Request) -> web.Response:
    """Show success page then redirect to dashboard."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Infusion — Upstox Login Successful</title>
  <style>
    body{background:#080c16;color:#e2e8f0;font-family:Segoe UI,system-ui,sans-serif;
         min-height:100vh;display:flex;align-items:center;justify-content:center}
    .card{background:#10182a;border:1px solid #14532d;border-radius:18px;
          padding:48px;max-width:440px;width:100%;text-align:center}
    .icon{font-size:56px;margin-bottom:16px}.title{font-size:22px;font-weight:800;color:#4ade80}
    .sub{font-size:13px;color:#94a3b8;margin:12px 0 28px;line-height:1.6}
    .btn{display:inline-block;padding:12px 28px;background:#14532d;color:#4ade80;
         font-size:14px;font-weight:700;border-radius:10px;text-decoration:none;border:1px solid #4ade80}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <div class="title">Upstox Login Successful</div>
    <div class="sub">Access token stored. Infusion will reconnect to Upstox Market Data Feed V3.</div>
    <a class="btn" href="http://localhost:3000">Go to Dashboard →</a>
  </div>
  <script>setTimeout(() => { window.location.href = 'http://localhost:3000'; }, 2500);</script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "oauth-server", "broker": "upstox"})
