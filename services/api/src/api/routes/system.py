"""System / capability routes — EBIE EB-0.

Surfaces the provider capability registry and live dynamic-subscription
status (see ingestion/capability_registry.py, ingestion/subscription_
registry.py) so the dashboard and later EBIE tiers can see, in one place,
what the data pipeline is actually entitled to and doing -- rather than
guessing from source code (see docs/EBIE-IMPLEMENTATION-QUESTIONS.md
Q2.1/Q7.1, which this endpoint exists to finally answer at runtime).
"""

import msgpack
from aiohttp import web

routes = web.RouteTableDef()


@routes.get("/api/system/capability")
async def capability(request):
    """Provider capability registry + live subscription/reconnect status."""
    redis = request.app["redis"]

    # Broker isn't known to this route without importing ingestion's own
    # config, so check both providers this codebase actually supports
    # (see IngestionSettings.broker_primary) rather than hardcoding one.
    cap_data = None
    cap_provider = None
    for provider in ("upstox", "mock"):
        raw = await redis.get(f"infusion:capability:{provider}")
        if raw:
            try:
                cap_data = msgpack.unpackb(raw, raw=False)
                cap_provider = provider
                break
            except Exception:
                continue

    sub_raw = await redis.get("infusion:subscription:status")
    sub_data = None
    if sub_raw:
        try:
            sub_data = msgpack.unpackb(sub_raw, raw=False)
        except Exception:
            sub_data = None

    return web.json_response(
        {
            "capability": cap_data,
            "capability_available": cap_data is not None,
            "capability_provider": cap_provider,
            "subscription": sub_data,
            "subscription_available": sub_data is not None,
        }
    )
