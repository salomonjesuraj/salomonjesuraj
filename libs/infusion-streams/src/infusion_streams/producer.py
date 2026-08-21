"""Redis Stream producer with envelope encoding."""

from __future__ import annotations

import structlog
from infusion_models.events import EventType
from redis.asyncio import Redis

from infusion_streams.codec import encode_event

logger = structlog.get_logger()


class StreamProducer:
    """Publishes events to a Redis stream with versioned envelope."""

    def __init__(self, redis: Redis, stream: str, maxlen: int):
        self.redis = redis
        self.stream = stream
        self.maxlen = maxlen
        self._count = 0

    async def publish(
        self,
        event_type: EventType,
        payload: dict,
        received_at_us: int,
    ) -> str:
        """Encode and XADD to stream. Returns message ID."""
        encoded = encode_event(event_type, payload, received_at_us)
        msg_id = await self.redis.xadd(
            self.stream,
            {"data": encoded},
            maxlen=self.maxlen,
            approximate=True,
        )
        self._count += 1
        return msg_id

    async def publish_raw(self, data: bytes) -> str:
        """XADD pre-encoded bytes (for hot-path optimization)."""
        msg_id = await self.redis.xadd(
            self.stream,
            {"data": data},
            maxlen=self.maxlen,
            approximate=True,
        )
        self._count += 1
        return msg_id

    @property
    def published_count(self) -> int:
        return self._count
