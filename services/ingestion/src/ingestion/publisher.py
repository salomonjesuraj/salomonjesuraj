"""Tick publisher — publishes RawTick to tick:raw stream."""

import structlog

from infusion_models.tick import RawTickV1
from infusion_models.events import EventType
from infusion_streams.producer import StreamProducer
from infusion_streams.constants import STREAM_TICK_RAW, MAXLEN_TICK_RAW

logger = structlog.get_logger()


class TickPublisher:
    """Publishes RawTick to tick:raw stream."""

    def __init__(self, redis):
        self.redis = redis
        self.producer = StreamProducer(redis, STREAM_TICK_RAW, MAXLEN_TICK_RAW)
        self._malformed = 0

    async def publish(self, tick: RawTickV1):
        """Publish tick to stream."""
        try:
            await self.producer.publish(
                event_type=EventType.RAW_TICK,
                payload=tick.model_dump(),
                received_at_us=tick.received_at_us,
            )
        except Exception as e:
            self._malformed += 1
            logger.error("tick_publish_failed", error=str(e), instrument=tick.instrument_key)

    @property
    def stats(self) -> dict:
        return {
            "published": self.producer.published_count,
            "publish_errors": self._malformed,
        }
