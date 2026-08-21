"""Central registry mapping (event_type, version) → schema class.

Rules:
  1. Every schema class is a frozen Pydantic model.
  2. Versions are monotonically increasing integers.
  3. A new version is created when fields are added or semantics change.
  4. The CURRENT_VERSIONS dict is the source of truth for what producers emit.
  5. Consumers MUST handle current version AND current-1 (one version back).
"""

from __future__ import annotations

from infusion_models.alert import AlertOutboundV1
from infusion_models.events import EventType
from infusion_models.feature import FeatureVectorV1
from infusion_models.sector import SectorStateV1
from infusion_models.signal import ScanSignalV1, ScanSignalV2
from infusion_models.tick import NormalizedTickV1, RawTickV1

# What version producers currently emit
CURRENT_VERSIONS: dict[EventType, int] = {
    EventType.RAW_TICK: 1,
    EventType.NORMALIZED_TICK: 1,
    EventType.FEATURE_COMPUTED: 1,
    EventType.SCAN_SIGNAL: 2,
    EventType.SCAN_SUPPRESSED: 2,
    EventType.SECTOR_STATE: 1,
    EventType.CONVICTION_RANKED: 1,
    EventType.ALERT_OUTBOUND: 1,
}

# Schema class lookup: (event_type, version) → Pydantic model class
SCHEMA_REGISTRY: dict[tuple[EventType, int], type] = {
    (EventType.RAW_TICK, 1): RawTickV1,
    (EventType.NORMALIZED_TICK, 1): NormalizedTickV1,
    (EventType.FEATURE_COMPUTED, 1): FeatureVectorV1,
    (EventType.SCAN_SIGNAL, 1): ScanSignalV1,
    (EventType.SCAN_SIGNAL, 2): ScanSignalV2,
    (EventType.SCAN_SUPPRESSED, 2): ScanSignalV2,
    (EventType.SECTOR_STATE, 1): SectorStateV1,
    (EventType.CONVICTION_RANKED, 1): SectorStateV1,  # placeholder
    (EventType.ALERT_OUTBOUND, 1): AlertOutboundV1,
}


class SchemaVersionError(Exception):
    """Raised when a message has an unrecognized schema version."""

    pass


def get_schema(event_type: EventType, version: int) -> type:
    """Resolve schema class for a given event type and version."""
    key = (event_type, version)
    if key not in SCHEMA_REGISTRY:
        known = [v for (e, v) in SCHEMA_REGISTRY if e == event_type]
        raise SchemaVersionError(
            f"Unknown schema: {event_type} v{version}. Known versions: {known}"
        )
    return SCHEMA_REGISTRY[key]
