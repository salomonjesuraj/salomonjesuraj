"""Real checks against the dashboard's nginx (services/dashboard/nginx.conf),
not the api container directly -- confirms the actual proxy path a
browser uses (dashboard:3000/api/... -> api:8000/...) resolves, matching
docs/IMPLEMENTATION-IMPROVEMENT-PLAN-2026-08-21.md's own Phase 5 item 6.

Deliberately not covered: the /ws WebSocket upgrade proxy. A real
WebSocket handshake test needs a dedicated ws client (aiohttp supports
one, but exercising an actual live tick subscription round-trip is a
larger, separate test than this file's plain-HTTP scope) -- disclosed
here as a real, known gap rather than silently skipped.
"""

from __future__ import annotations

import re

# P0 audit fix (2026-08-28): the legacy vanilla-JS dashboard (services/
# dashboard/, mount point id="newShell", hand-written /js/app.js) was
# deleted outright on 2026-08-27 -- see docker-compose.yml's own
# comment above the `ui:` service. This whole file was still asserting
# against that removed shell, so it had been silently exercising
# nothing real: id="newShell" never matches the real Vite dashboard's
# actual HTML (ui/index.html's own <div id="root">), and /js/app.js was
# never a real route the fingerprinted-asset nginx config in ui/
# nginx.conf serves. Both tests below now assert against what the
# stack actually serves.
_VITE_SCRIPT_SRC_RE = re.compile(r'<script[^>]+type="module"[^>]+src="(/assets/[^"]+)"')


async def test_dashboard_serves_the_real_spa_shell(dashboard_client) -> None:
    async with dashboard_client.get("/") as resp:
        assert resp.status == 200
        body = await resp.text()
    assert "<html" in body.lower()
    # ui/index.html's own real mount point (see that file) -- confirms
    # this is genuinely Infusion's Vite-built dashboard HTML, not an
    # nginx default/error page.
    assert 'id="root"' in body


async def test_dashboard_proxies_api_requests_to_the_real_api_container(dashboard_client) -> None:
    """The exact path a browser takes -- through nginx's `/api/` location
    block, not a direct call to the api container's own port."""
    async with dashboard_client.get("/api/diagnostics") as resp:
        assert resp.status == 200
        body = await resp.json()
    assert isinstance(body["symbols_loaded"], int)
    assert body["symbols_loaded"] > 0


async def test_dashboard_proxies_a_post_request_correctly(dashboard_client) -> None:
    """POST bodies specifically -- a proxy misconfiguration (e.g. a
    missing request-body pass-through) can break POST while GET still
    works fine, so this is a distinct check from the GET test above."""
    async with dashboard_client.post(
        "/api/ai/query", json={"question": "what is the market regime"}
    ) as resp:
        assert resp.status == 200
        body = await resp.json()
    assert body["source"] == "deterministic"


async def test_dashboard_serves_real_javascript_not_a_404_page(dashboard_client) -> None:
    """Vite fingerprints the real entrypoint's filename with a content
    hash (ui/nginx.conf's own /assets/ location comment) -- there's no
    fixed path to probe, so the real asset URL is read out of the
    actually-served index.html's own <script type="module"> tag first,
    the same way a real browser resolves it. Confirms /assets/ static
    serving is real, not silently falling through to the SPA's
    index.html fallback (which would also return 200, making a bare
    status check useless here -- the content-type/real-JS-syntax check
    is what actually matters)."""
    async with dashboard_client.get("/") as resp:
        assert resp.status == 200
        shell = await resp.text()
    match = _VITE_SCRIPT_SRC_RE.search(shell)
    assert match is not None, "index.html has no fingerprinted /assets/ module script tag"
    asset_path = match.group(1)

    async with dashboard_client.get(asset_path) as resp:
        assert resp.status == 200
        body = await resp.text()
    assert "<html" not in body.lower()
    assert "import" in body or "export" in body or "function" in body
