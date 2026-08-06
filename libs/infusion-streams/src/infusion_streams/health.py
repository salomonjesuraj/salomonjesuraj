"""Stream health utilities — lag monitoring."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


async def log_consumer_lag(redis, stream: str, group: str) -> int:
    """Check and log consumer group lag. Returns pending count."""
    try:
        groups = await redis.xinfo_groups(stream)
        for g in groups:
            name = g.get("name") or g.get(b"name", b"").decode()
            if name == group:
                pending = g.get("pending") or g.get(b"pending", 0)
                if pending > 100:
                    logger.warning("consumer_lag_high", stream=stream, group=group, pending=pending)
                elif pending > 0:
                    logger.debug("consumer_lag", stream=stream, group=group, pending=pending)
                return pending
    except Exception as e:
        logger.warning("consumer_lag_check_failed", stream=stream, group=group, error=str(e))
    return 0
