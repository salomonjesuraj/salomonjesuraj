"""Stream message envelope codec — encode/decode with versioned envelope."""

import time

import msgpack
from infusion_models.events import EventType
from infusion_models.schema_registry import CURRENT_VERSIONS


def encode_event(
    event_type: EventType,
    payload: dict,
    received_at_us: int,
) -> bytes:
    """Encode a stream event with versioned envelope."""
    envelope = {
        "v": CURRENT_VERSIONS[event_type],
        "t": event_type.value,
        "ts": int(time.time() * 1_000_000),
        "rx": received_at_us,
        "d": payload,
    }
    return msgpack.packb(envelope, use_bin_type=True)


def decode_event(raw: bytes) -> tuple[EventType, int, int, int, dict]:
    """Decode a stream event.

    Returns:
        (event_type, version, created_at_us, received_at_us, payload)
    """
    envelope = msgpack.unpackb(raw, raw=False)
    return (
        EventType(envelope["t"]),
        envelope["v"],
        envelope["ts"],
        envelope["rx"],
        envelope["d"],
    )
