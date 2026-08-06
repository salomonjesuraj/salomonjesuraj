"""All event types that flow through Infusion streams."""

from enum import StrEnum


class EventType(StrEnum):
    """Event type tags used in stream message envelopes."""

    RAW_TICK = "raw_tick"
    NORMALIZED_TICK = "normalized_tick"
    FEATURE_COMPUTED = "feature_computed"
    SCAN_SIGNAL = "scan_signal"
    SCAN_SUPPRESSED = "scan_suppressed"
    SECTOR_STATE = "sector_state"
    CONVICTION_RANKED = "conviction_ranked"
    ALERT_OUTBOUND = "alert_outbound"
