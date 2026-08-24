"""Capability registry publisher — EBIE EB-0.

Publishes two things to Redis on a fixed interval (same shape as
HealthReporter, see infusion_common.health):

  1. infusion:capability:{provider} -- the static, operator-declared
     ProviderCapabilityV1 record (see infusion_models.capability).
  2. infusion:subscription:status -- live, dynamic state: subscription
     tier counts from the SubscriptionRegistry, plus reconnect/gap health
     read straight off the adapter (getattr with a default, since
     MockAdapter doesn't track gaps the way UpstoxAdapter does).

Republished on an interval purely so a crashed ingestion process's TTL
expires and consumers see the registry go missing rather than trusting a
silently stale record -- the same reasoning HealthReporter already uses.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import msgpack
import structlog
from infusion_common.timing import now_us
from infusion_models.capability import ProviderCapabilityV1
from infusion_streams.constants import KEY_CAPABILITY_PREFIX, KEY_SUBSCRIPTION_STATUS

logger = structlog.get_logger()


class CapabilityRegistry:
    """Publishes provider capability + live subscription/reconnect status."""

    def __init__(
        self,
        redis: Any,
        capability: ProviderCapabilityV1,
        subscription_registry: Any,
        adapter: Any,
        interval_sec: int = 60,
        ttl_sec: int = 180,
    ) -> None:
        self.redis = redis
        self.capability = capability
        self.subscription_registry = subscription_registry
        self.adapter = adapter
        self.interval_sec = interval_sec
        self.ttl_sec = ttl_sec
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("capability_registry_started", provider=self.capability.provider)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            logger.info("capability_registry_stopped", provider=self.capability.provider)

    async def _loop(self) -> None:
        while True:
            try:
                await self._publish_once()
            except Exception as e:
                logger.warning("capability_publish_failed", error=str(e))
            await asyncio.sleep(self.interval_sec)

    async def _publish_once(self) -> None:
        now = now_us()

        cap_record = self.capability.model_copy(update={"checked_at_us": now})
        await self.redis.set(
            f"{KEY_CAPABILITY_PREFIX}{self.capability.provider}",
            msgpack.packb(cap_record.model_dump()),
            ex=self.ttl_sec,
        )

        sub_status = {
            "provider": self.capability.provider,
            "tier_counts": self.subscription_registry.tier_counts(),
            "reconnect_count": getattr(self.adapter, "_reconnect_count", 0),
            "last_gap_ms": getattr(self.adapter, "_last_gap_ms", 0),
            "cumulative_gap_ms": getattr(self.adapter, "_cumulative_gap_ms", 0),
            "updated_at_us": now,
        }
        await self.redis.set(
            KEY_SUBSCRIPTION_STATUS,
            msgpack.packb(sub_status),
            ex=self.ttl_sec,
        )
