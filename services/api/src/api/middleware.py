"""Application-wide aiohttp middleware for the api service.

Graceful-degradation fix (2026-08-25): a live-error sweep of the dashboard
found the api container logging ~2 redis.exceptions.TimeoutError/min
(bursty -- one cluster of 5 within 17s), each one propagating uncaught
out of whatever route handler happened to be mid-request straight to
aiohttp's own generic exception handler. That handler returns a plain
text/HTML "500 Internal Server Error" page (see
aiohttp.web_protocol.RequestHandler's INTERNAL_SERVER_ERROR branch) --
NOT JSON -- so every dashboard fetch().then(r => r.json()) call hitting
this failure mode got a JSON parse error stacked on top of the real
Redis hiccup, with nothing logged beyond aiohttp's own bare traceback.

Root cause of the underlying Redis stalls (separately investigated, not
fixed here): `save 300 100` snapshotting under heavy write load
(~1300 ops/sec, ~910k changes/save-interval) with used_memory at ~84%
of a 1GB maxmemory cap -- the periodic BGSAVE fork() is the leading
hypothesis for the multi-second stalls that exceed redis-py's default
5s socket_timeout. That is an infra-tuning question (snapshot cadence,
maxmemory, or connection pool sizing) with real tradeoffs, and is
deliberately left for a separate decision -- this fix only addresses
the API-visible *symptom*: a bare Redis hiccup should degrade to an
honest, structured, retryable response, not a broken page and a silent
stack trace.

This is scoped to Redis specifically (the one component actually
observed stalling this session) -- it does not touch Postgres error
handling, which already has its own per-route `pool is None` /
try-except handling throughout routes/backtest.py, routes/ebie_state.py,
etc.
"""

from __future__ import annotations

import redis.exceptions
import structlog
from aiohttp import web
from aiohttp.typedefs import Handler

logger = structlog.get_logger()


@web.middleware
async def redis_error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Turn an uncaught redis.exceptions.RedisError into a structured,
    honest 503 instead of letting it reach aiohttp's own plain-text 500
    page.

    Deliberately catches the RedisError base class, not just
    TimeoutError -- ConnectionError/BusyLoadingError are the same class
    of "the cache is briefly unreachable, the next poll will likely
    succeed" transient condition this fix targets, per
    infusion_common.errors.classify_error's own existing ErrorCategory.TRANSIENT
    treatment of the whole family. A route that already catches
    RedisError itself (several do, e.g. routes/backtest.py's own
    "Redis not available." responses) never reaches this middleware --
    it only sees what the handler didn't already handle.
    """
    try:
        return await handler(request)
    except redis.exceptions.RedisError as exc:
        logger.warning(
            "redis_request_degraded",
            path=request.path,
            method=request.method,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return web.json_response(
            {
                "available": False,
                "error": "temporarily_unavailable",
                "source": "redis",
                "reason": (f"Redis was briefly unreachable ({type(exc).__name__}); retry shortly."),
            },
            status=503,
        )
