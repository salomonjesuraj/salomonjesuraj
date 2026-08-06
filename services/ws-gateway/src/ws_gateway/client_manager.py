"""WebSocket client manager — registry, batching, fan-out."""

import asyncio
import json
import time

import structlog

logger = structlog.get_logger()


class ClientManager:
    """Manages connected WebSocket clients and their subscriptions."""

    def __init__(self):
        self._clients: dict[str, object] = {}  # id -> ws
        self._subscriptions: dict[str, set[str]] = {}  # id -> set of symbols
        self._batch_buffer: dict[str, dict] = {}  # symbol -> latest tick data
        self._batch_lock = asyncio.Lock()
        self._messages_sent = 0

    async def add(self, client_id: str, ws):
        self._clients[client_id] = ws
        self._subscriptions[client_id] = set()  # empty = subscribe to all
        logger.info("client_connected", client_id=client_id, total=len(self._clients))

    async def remove(self, client_id: str):
        self._clients.pop(client_id, None)
        self._subscriptions.pop(client_id, None)
        logger.info("client_disconnected", client_id=client_id, total=len(self._clients))

    async def handle_subscribe(self, client_id: str, symbols: list[str]):
        """Client subscribes to specific symbols."""
        if client_id in self._subscriptions:
            self._subscriptions[client_id] = set(symbols)

    async def buffer_tick(self, symbol: str, data: dict):
        """Buffer a tick update for batched delivery."""
        async with self._batch_lock:
            self._batch_buffer[symbol] = data

    async def flush_batch(self):
        """Send batched tick updates to all connected clients."""
        async with self._batch_lock:
            if not self._batch_buffer:
                return
            buffer = self._batch_buffer
            self._batch_buffer = {}

        if not self._clients:
            return

        message = json.dumps({
            "type": "tick_batch",
            "data": buffer,
            "ts": int(time.time() * 1000),
        })

        dead_clients = []
        for client_id, ws in self._clients.items():
            try:
                if not ws.closed:
                    await ws.send_str(message)
                    self._messages_sent += 1
            except Exception:
                dead_clients.append(client_id)

        for cid in dead_clients:
            await self.remove(cid)

    async def send_signal(self, signal_data: dict):
        """Push signal immediately to all clients."""
        if not self._clients:
            return

        message = json.dumps({
            "type": "signal",
            "data": signal_data,
            "ts": int(time.time() * 1000),
        })

        dead_clients = []
        for client_id, ws in self._clients.items():
            try:
                if not ws.closed:
                    await ws.send_str(message)
                    self._messages_sent += 1
            except Exception:
                dead_clients.append(client_id)

        for cid in dead_clients:
            await self.remove(cid)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def messages_sent(self) -> int:
        return self._messages_sent
