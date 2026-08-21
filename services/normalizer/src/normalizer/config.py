"""Normalizer service configuration."""

from infusion_common.config import InfusionSettings


class NormalizerSettings(InfusionSettings):
    service_name: str = "normalizer"

    # Throttling
    tier2_min_interval_ms: int = 500
    tier3_min_interval_ms: int = 2000

    # Dedup
    dedup_ring_size: int = 20  # per-symbol ring buffer size

    # Consumer
    batch_size: int = 200
    block_ms: int = 5
