"""Health reporter — background task that writes heartbeat to Redis."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any

import msgpack
import structlog
from infusion_streams.constants import KEY_HEALTH_PREFIX

logger = structlog.get_logger()


class HealthReporter:
    """Publishes service health heartbeats to Redis."""

    def __init__(
        self,
        redis: Any,
        service_name: str,
        interval_sec: int = 10,
        ttl_sec: int = 30,
    ):
        self.redis = redis
        self.service_name = service_name
        self.interval_sec = interval_sec
        self.ttl_sec = ttl_sec
        self._start_time = time.time()
        self._details_fn: Callable[[], dict[str, Any]] | None = None
        self._task: asyncio.Task[None] | None = None

    def set_details_fn(self, fn: Callable[[], dict[str, Any]]) -> None:
        """Set a callable that returns extra health details dict."""
        self._details_fn = fn

    async def start(self) -> None:
        """Start background health reporting loop."""
        self._task = asyncio.create_task(self._loop())
        logger.info("health_reporter_started", service=self.service_name)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            logger.info("health_reporter_stopped", service=self.service_name)

    async def _loop(self) -> None:
        while True:
            try:
                details: dict[str, Any] = {}
                if self._details_fn:
                    details = self._details_fn()

                payload = {
                    "status": "healthy",
                    "uptime_sec": round(time.time() - self._start_time, 1),
                    **details,
                }

                key = f"{KEY_HEALTH_PREFIX}{self.service_name}"
                await self.redis.set(
                    key,
                    msgpack.packb(payload),
                    ex=self.ttl_sec,
                )
            except Exception as e:
                logger.warning("health_report_failed", error=str(e))

            await asyncio.sleep(self.interval_sec)
