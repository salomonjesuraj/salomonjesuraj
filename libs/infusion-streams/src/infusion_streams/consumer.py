"""Redis Stream consumer with consumer group semantics and DLQ support.

Usage:
    consumer = StreamConsumer(redis, stream, group, name)
    await consumer.ensure_group()
    async for event_type, version, rx_us, payload, ack in consumer.consume():
        process(payload)
        await ack()
"""

from __future__ import annotations

import asyncio
import base64
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

import msgpack
import structlog
from infusion_common.errors import ErrorCategory, classify_error
from infusion_models.events import EventType
from redis.asyncio import Redis

from infusion_streams.codec import decode_event
from infusion_streams.constants import DLQ_PREFIX, MAXLEN_DLQ

logger = structlog.get_logger()


class StreamConsumer:
    """
    Redis Stream consumer with consumer group semantics and DLQ support.

    Two usage patterns:
    1. Async generator via consume() — caller processes and calls ack
    2. handle_with_retry() — automatic retry + DLQ on failure
    """

    def __init__(
        self,
        redis: Redis,
        stream: str,
        group: str,
        consumer_name: str,
        batch_size: int = 100,
        block_ms: int = 5,
        max_retries: int = 3,
    ):
        self.redis = redis
        self.stream = stream
        self.group = group
        self.consumer_name = consumer_name
        self.batch_size = batch_size
        self.block_ms = block_ms
        self.max_retries = max_retries
        self.dlq_stream = DLQ_PREFIX + stream.replace("infusion:stream:", "")
        self._retry_counts: dict[str, int] = {}
        self._processed = 0
        self._errors = 0

    async def ensure_group(self) -> None:
        """Create consumer group if it doesn't exist."""
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
            logger.info("consumer_group_created", stream=self.stream, group=self.group)
        except Exception as e:
            if "BUSYGROUP" in str(e):
                pass  # Group already exists
            else:
                raise

    async def consume(
        self,
    ) -> AsyncIterator[tuple[EventType, int, int, dict[str, Any], Callable[[], Awaitable[None]]]]:
        """
        Async generator yielding (event_type, version, rx_us, payload, ack_fn).

        Caller processes the payload, then calls ack_fn() on success.
        On failure, caller should NOT call ack_fn — the message stays in PEL.
        """
        while True:
            try:
                messages = await self.redis.xreadgroup(
                    groupname=self.group,
                    consumername=self.consumer_name,
                    streams={self.stream: ">"},
                    count=self.batch_size,
                    block=self.block_ms,
                )
            except Exception as e:
                err = classify_error(e)
                logger.error("stream_read_error", **err.to_log_dict())
                await asyncio.sleep(1)
                continue

            if not messages:
                continue

            for _stream_name, entries in cast(
                list[tuple[Any, list[tuple[Any, dict[Any, Any]]]]], messages
            ):
                for msg_id, fields in entries:
                    raw_data = fields.get(b"data") or fields.get("data")
                    if raw_data is None:
                        await self._send_to_dlq(
                            msg_id, b"", "MALFORMED_DATA", "Missing 'data' field"
                        )
                        await self.redis.xack(self.stream, self.group, msg_id)
                        continue

                    try:
                        event_type, version, _ts, rx, payload = decode_event(raw_data)
                    except Exception as e:
                        await self._send_to_dlq(msg_id, raw_data, "MALFORMED_DATA", str(e))
                        await self.redis.xack(self.stream, self.group, msg_id)
                        self._errors += 1
                        continue

                    async def make_ack(mid: Any = msg_id) -> None:
                        await self.redis.xack(self.stream, self.group, mid)
                        self._processed += 1

                    yield event_type, version, rx, payload, make_ack

    async def handle_with_retry(
        self,
        msg_id: str,
        raw_data: bytes,
        handler: Callable[[bytes], Awaitable[Any]],
    ) -> bool:
        """Process message with retry + DLQ. Returns True on success."""
        try:
            await handler(raw_data)
            self._retry_counts.pop(msg_id, None)
            self._processed += 1
            return True
        except Exception as e:
            err = classify_error(e)
            if err.category == ErrorCategory.MALFORMED_DATA:
                await self._send_to_dlq(msg_id, raw_data, "MALFORMED_DATA", str(e))
                return False
            if err.category == ErrorCategory.FATAL:
                await self._send_to_dlq(msg_id, raw_data, "FATAL", str(e))
                raise
            # Retryable
            count = self._retry_counts.get(msg_id, 0) + 1
            self._retry_counts[msg_id] = count
            if count >= self.max_retries:
                await self._send_to_dlq(msg_id, raw_data, err.category.value, str(e))
                self._retry_counts.pop(msg_id, None)
                return False
            self._errors += 1
            return False

    async def _send_to_dlq(
        self, msg_id: str | bytes, raw_data: bytes, category: str, reason: str
    ) -> None:
        """Move poison message to dead letter stream."""
        retry_key = msg_id if isinstance(msg_id, str) else msg_id.decode()
        dlq_entry = {
            "original_stream": self.stream,
            "original_id": retry_key,
            "original_payload": base64.b64encode(raw_data).decode() if raw_data else "",
            "consumer_group": self.group,
            "consumer_name": self.consumer_name,
            "failure_category": category,
            "failure_reason": reason,
            "retry_count": self._retry_counts.get(retry_key, 0),
            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stack_trace": traceback.format_exc(),
        }
        try:
            await self.redis.xadd(
                self.dlq_stream,
                {"data": msgpack.packb(dlq_entry)},
                maxlen=MAXLEN_DLQ,
                approximate=True,
            )
            logger.error(
                "message_dlq",
                message_id=msg_id,
                stream=self.stream,
                category=category,
                reason=reason,
            )
        except Exception as dlq_err:
            logger.critical(
                "dlq_write_failed",
                original_error=reason,
                dlq_error=str(dlq_err),
            )

    @property
    def stats(self) -> dict[str, int]:
        return {
            "processed": self._processed,
            "errors": self._errors,
            "pending_retries": len(self._retry_counts),
        }
