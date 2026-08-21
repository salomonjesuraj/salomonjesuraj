"""Subscription tier/mode registry — EBIE EB-0 infrastructure.

Prep for EBIE's tiered scanning architecture (blueprint Section 4.2):
Tier 1 = universe scan (cheap, all symbols), Tier 2 = candidate promotion
(richer analysis, no extra WS mode needed yet), Tier 3 = armed setups
(deepest available depth).

EB-0 only builds the registry and the adapter-level subscribe/unsubscribe/
change_mode capability -- it does NOT decide who gets promoted. Every
symbol starts and stays at Tier 1 until EB-6 (microstructure) actually
wires promotion logic, per the authorized decision:

    "Milestone A can still scan all ~208 symbols uniformly at Tier-1
     depth... do not make early EB-0 correctness depend on constant
     runtime subscription churn."

promote() exists now so EB-6 can call it directly without another round
of adapter/registry plumbing -- nothing in EB-0 calls it.
"""

from __future__ import annotations

import time

import structlog
from infusion_streams.constants import KEY_SUBSCRIPTION_TIER_PREFIX

logger = structlog.get_logger()

TIER_1 = "tier1"  # universe scan, cheapest mode
TIER_2 = "tier2"  # candidate promotion, richer analysis
TIER_3 = "tier3"  # armed setups, deepest available depth

DEFAULT_MODE = "full"  # Upstox V3: 5-level depth, the EB-0 baseline
DEEP_MODE = "full_d30"  # Upstox Plus only -- see ProviderCapabilityV1.supports_full_d30


class SubscriptionRegistry:
    """Tracks each symbol's current scanning tier and Upstox feed mode.

    Mirrored into Redis (KEY_SUBSCRIPTION_TIER_PREFIX per symbol) so the
    api/dashboard can inspect it without a direct line to the ingestion
    process.
    """

    def __init__(self, redis, adapter):
        self.redis = redis
        self.adapter = adapter
        self._tier: dict[str, str] = {}
        self._mode: dict[str, str] = {}

    async def initialize(self, instrument_keys: list[str]) -> None:
        """Set every symbol to Tier 1 / default mode at boot -- the only
        behavior EB-0 actually turns on. Promotion is EB-6's job."""
        now = int(time.time())
        pipe = self.redis.pipeline()
        for key in instrument_keys:
            self._tier[key] = TIER_1
            self._mode[key] = DEFAULT_MODE
            pipe.hset(
                f"{KEY_SUBSCRIPTION_TIER_PREFIX}{key}",
                mapping={"tier": TIER_1, "mode": DEFAULT_MODE, "updated_at": now},
            )
        if instrument_keys:
            await pipe.execute()
        logger.info(
            "subscription_registry_initialized",
            count=len(instrument_keys),
            tier=TIER_1,
        )

    async def promote(self, instrument_key: str, tier: str, mode: str | None = None) -> None:
        """Promote (or demote) a single symbol's tier/mode.

        Not called by anything in EB-0 -- see module docstring.
        """
        target_mode = mode or self._mode.get(instrument_key, DEFAULT_MODE)
        current_mode = self._mode.get(instrument_key, DEFAULT_MODE)
        if target_mode != current_mode:
            await self.adapter.change_mode([instrument_key], target_mode)

        self._tier[instrument_key] = tier
        self._mode[instrument_key] = target_mode
        await self.redis.hset(
            f"{KEY_SUBSCRIPTION_TIER_PREFIX}{instrument_key}",
            mapping={"tier": tier, "mode": target_mode, "updated_at": int(time.time())},
        )
        logger.info(
            "subscription_promoted",
            symbol=instrument_key,
            tier=tier,
            mode=target_mode,
        )

    def tier_counts(self) -> dict[str, int]:
        counts = {TIER_1: 0, TIER_2: 0, TIER_3: 0}
        for t in self._tier.values():
            counts[t] = counts.get(t, 0) + 1
        return counts
