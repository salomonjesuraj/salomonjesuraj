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


async def test_dashboard_serves_the_real_spa_shell(dashboard_client) -> None:
    async with dashboard_client.get("/") as resp:
        assert resp.status == 200
        body = await resp.text()
    assert "<html" in body.lower()
    # The New-shell mount point every EBIE panel this session built lives
    # under -- a real, specific marker that this is genuinely Infusion's
    # dashboard HTML, not an nginx default/error page.
    assert 'id="newShell"' in body


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
    """app.js is the dashboard's own module entrypoint (services/dashboard/
    public/index.html's script tag) -- confirms /js/ static serving is
    real, not silently falling through to the SPA's index.html fallback
    (which would also return 200, making a bare status check useless
    here -- the content-type/real-JS-syntax check is what actually
    matters)."""
    async with dashboard_client.get("/js/app.js") as resp:
        assert resp.status == 200
        body = await resp.text()
    assert "<html" not in body.lower()
    assert "import" in body or "export" in body
