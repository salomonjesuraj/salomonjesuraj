"""Deterministic stock-event calendar helpers.

This intentionally replaces keyword-based news confidence for execution gates.
Events are stored manually/API-fed in Redis and are treated as binary risk
windows, not sentiment.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

EVENT_KEY_PREFIX = "infusion:event-calendar:"


def _days_to_event(event_date: str) -> int | None:
    try:
        return (date.fromisoformat(str(event_date)[:10]) - date.today()).days
    except Exception:
        return None


async def get_event_risk(redis: Any, symbol: str) -> dict[str, Any]:
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return {"symbol": "", "entry_allowed": True, "event_type": "NONE"}
    raw = await redis.get(f"{EVENT_KEY_PREFIX}{symbol}")
    if not raw:
        return {
            "symbol": symbol,
            "entry_allowed": True,
            "event_type": "NONE",
            "next_event_date": "",
            "days_to_event": None,
            "block_reason": "",
            "source": "calendar_empty",
        }
    try:
        payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception:
        payload = {}
    next_date = str(payload.get("next_event_date") or payload.get("event_date") or "")
    days = _days_to_event(next_date)
    event_type = str(payload.get("event_type") or "EVENT").upper()
    blocked = days is not None and -1 <= days <= 2
    return {
        "symbol": symbol,
        "entry_allowed": not blocked,
        "event_type": event_type,
        "next_event_date": next_date,
        "days_to_event": days,
        "block_reason": f"hard block from T-2 to T+1 around {event_type.lower()}"
        if blocked
        else "",
        "source": str(payload.get("source") or "manual_calendar"),
        "note": str(payload.get("note") or ""),
    }
