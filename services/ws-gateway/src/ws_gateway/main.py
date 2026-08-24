"""WS Gateway — reads streams, pushes to browser via WebSocket.

Ports:
  - WS: ws://host:8001/ws
  - Health: http://host:8001/health
"""

import asyncio
import json
import uuid
from typing import Any, cast

import structlog
from aiohttp import web
from infusion_common.health import HealthReporter
from infusion_common.logging import setup_logging
from infusion_streams.constants import (
    CG_DASHBOARD,
    STREAM_TICK_NORMALIZED,
)
from infusion_streams.consumer import StreamConsumer
from redis.asyncio import Redis

from ws_gateway.client_manager import ClientManager
from ws_gateway.config import WSGatewaySettings

logger = structlog.get_logger()
Payload = dict[str, Any]


async def main() -> None:
    config = WSGatewaySettings()
    setup_logging(config.service_name, config.log_level, config.log_format)

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    await redis.ping()
    clients = ClientManager()

    # Health
    health = HealthReporter(redis, config.service_name)
    health.set_details_fn(
        lambda: {
            "clients": clients.client_count,
            "messages_sent": clients.messages_sent,
        }
    )
    await health.start()

    # Stream consumer for normalized ticks
    tick_consumer = StreamConsumer(
        redis,
        STREAM_TICK_NORMALIZED,
        CG_DASHBOARD,
        "ws-gateway-tick",
        batch_size=config.consumer_batch_size,
        block_ms=config.consumer_block_ms,
    )
    await tick_consumer.ensure_group()

    # Background: read ticks and buffer for batch delivery
    async def tick_reader() -> None:
        async for _event_type, _version, _rx_us, payload, ack in tick_consumer.consume():
            symbol = str(payload.get("symbol", ""))
            await clients.buffer_tick(
                symbol,
                {
                    "ltp": payload.get("ltp"),
                    "volume": payload.get("volume"),
                    "high": payload.get("high"),
                    "low": payload.get("low"),
                    "change_pct": round(
                        (payload.get("ltp", 0) - payload.get("close", 1))
                        / max(payload.get("close", 1), 0.01)
                        * 100,
                        2,
                    ),
                },
            )
            await ack()

    # Background: flush batched ticks every 100ms
    async def batch_flusher() -> None:
        interval = config.price_batch_ms / 1000.0
        while True:
            await asyncio.sleep(interval)
            await clients.flush_batch()

    # WebSocket handler
    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        client_id = uuid.uuid4().hex[:8]
        await clients.add(client_id, ws)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data_raw = json.loads(msg.data)
                        data = cast(Payload, data_raw) if isinstance(data_raw, dict) else {}
                        if data.get("type") == "subscribe":
                            symbols_raw = data.get("symbols", [])
                            symbols = (
                                [str(symbol) for symbol in symbols_raw]
                                if isinstance(symbols_raw, list)
                                else []
                            )
                            await clients.handle_subscribe(client_id, symbols)
                    except Exception:
                        pass
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            await clients.remove(client_id)

        return ws

    # Health endpoint
    async def health_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "healthy",
                "clients": clients.client_count,
                "messages_sent": clients.messages_sent,
            }
        )

    # Setup aiohttp app
    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.ws_host, config.ws_port)
    await site.start()

    logger.info("ws_gateway_started", host=config.ws_host, port=config.ws_port)

    # Run background tasks
    await asyncio.gather(
        tick_reader(),
        batch_flusher(),
    )


if __name__ == "__main__":
    asyncio.run(main())
