"""Upstox Market Data Feed V3 adapter for live ticks."""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import aiohttp
import structlog
from infusion_common.timing import now_us
from infusion_models.tick import RawTickV1

from ingestion.adapters.base import BrokerAdapter, ConnectionState
from ingestion.adapters.upstox_codec import DecodedFeed, ProtoDecodeError, decode_feed_response
from ingestion.config import IngestionSettings

logger = structlog.get_logger()
IST = timezone(timedelta(hours=5, minutes=30))
Payload = dict[str, Any]


class UpstoxAdapter(BrokerAdapter):
    """Upstox V3 WebSocket adapter."""

    name = "upstox"

    def __init__(self, config: IngestionSettings) -> None:
        self.config = config
        self.state = ConnectionState.INIT
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._access_token: str = ""
        self._tick_count = 0
        self._last_tick_time = 0.0
        self._reconnect_count = 0
        self._redis: Any = None
        self._last_auth_error = ""
        self._token_source = ""
        self._token_expiry_ts = 0
        # EBIE EB-0: reconnect-gap tracking. _reconnect_count previously
        # existed but was never incremented anywhere -- health() always
        # reported 0 regardless of how many reconnects actually happened.
        # Fixed here: connect() increments it (and computes the gap since
        # the prior disconnect) whenever this isn't the process's first
        # connect.
        self._has_connected_once = False
        self._disconnected_at: float = 0.0
        self._last_gap_ms: int = 0
        self._cumulative_gap_ms: int = 0

    def set_redis(self, redis: Any) -> None:
        """Provide Redis for OAuth token lookup."""
        self._redis = redis

    @staticmethod
    def _jwt_expiry(token: str) -> int:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload.encode()))
            return int(data.get("exp") or 0)
        except Exception:
            return 0

    async def _redis_token(self) -> str:
        if not self._redis:
            return ""
        auth_raw = await self._redis.get("infusion:auth:upstox")
        if not auth_raw:
            return ""
        auth_text = auth_raw.decode() if isinstance(auth_raw, bytes) else auth_raw
        try:
            auth_data = json.loads(auth_text)
            token = auth_data.get("access_token", "") if isinstance(auth_data, dict) else ""
            return str(token)
        except Exception:
            return ""

    async def authenticate(self) -> str:
        """Get access token, preferring Redis so OAuth refresh works without container recreate."""
        self.state = ConnectionState.AUTHENTICATING
        redis_token = await self._redis_token()
        env_token = self.config.upstox_access_token
        redis_exp = self._jwt_expiry(redis_token)
        env_exp = self._jwt_expiry(env_token)

        if redis_token and redis_exp >= env_exp:
            self._access_token = redis_token
            self._token_source = "redis"
            logger.info("upstox_token_from_redis")
        else:
            self._access_token = env_token
            self._token_source = "env" if env_token else ""

        if not self._access_token:
            self._last_auth_error = "missing_token"
            raise RuntimeError(
                "Upstox access token not configured. Login at http://localhost:5100/auth/login "
                "or set INFUSION_UPSTOX_ACCESS_TOKEN."
            )

        self._token_expiry_ts = self._jwt_expiry(self._access_token)
        if self._token_expiry_ts and self._token_expiry_ts <= int(time.time()):
            self._last_auth_error = "expired_token"
            raise RuntimeError(
                "Upstox access token expired; login at http://localhost:5100/auth/login."
            )

        self._last_auth_error = ""
        logger.info(
            "upstox_authenticated",
            token_source=self._token_source,
            token_expiry_utc=(
                datetime.fromtimestamp(self._token_expiry_ts, tz=UTC).isoformat()
                if self._token_expiry_ts
                else ""
            ),
        )
        return self._access_token

    async def connect(self) -> None:
        """Get authorized V3 WS URI and connect."""
        self.state = ConnectionState.CONNECTING

        # EBIE EB-0: this is a genuine reconnect (not the process's first
        # connect) the moment we've connected before -- record it and the
        # gap since the last disconnect so it's actually visible, instead
        # of the previously-dead _reconnect_count that always read 0.
        if self._has_connected_once:
            self._reconnect_count += 1
            if self._disconnected_at:
                gap_ms = int((time.time() - self._disconnected_at) * 1000)
                self._last_gap_ms = gap_ms
                self._cumulative_gap_ms += gap_ms
                logger.info(
                    "upstox_reconnect_gap",
                    gap_ms=gap_ms,
                    reconnect_count=self._reconnect_count,
                )
        self._has_connected_once = True

        self._session = aiohttp.ClientSession()

        auth_url = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

        async with self._session.get(auth_url, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                if resp.status == 401:
                    self._last_auth_error = "invalid_token"
                    if self._redis:
                        await self._redis.hset(
                            "infusion:auth:upstox:status",
                            mapping={
                                "state": "invalid_token",
                                "reason": body[:300],
                                "token_source": self._token_source,
                                "token_expiry_ts": str(self._token_expiry_ts),
                                "updated_at": str(int(time.time())),
                            },
                        )
                raise RuntimeError(f"Upstox WS auth failed: {resp.status} {body[:200]}")
            data = await resp.json()

        payload = cast(Payload, data.get("data", {})) if isinstance(data, dict) else {}
        ws_uri = (
            payload.get("authorized_redirect_uri") or payload.get("authorizedRedirectUri") or ""
        )
        if not ws_uri:
            raise RuntimeError("Upstox WS auth response did not include authorized_redirect_uri")

        self._ws = await self._session.ws_connect(
            ws_uri,
            heartbeat=self.config.ws_ping_interval_sec,
            autoclose=True,
            autoping=True,
        )
        self.state = ConnectionState.CONNECTING
        logger.info("upstox_ws_connected", uri=ws_uri[:60])

    async def subscribe(self, instrument_keys: list[str]) -> None:
        """Subscribe in batches using Upstox V3 binary request messages."""
        if not self._ws:
            raise RuntimeError("Upstox WebSocket is not connected")

        self.state = ConnectionState.SUBSCRIBING
        batch_size = self.config.subscribe_batch_size

        for i in range(0, len(instrument_keys), batch_size):
            batch = instrument_keys[i : i + batch_size]
            msg = {
                "guid": f"infusion-sub-{int(time.time() * 1000)}-{i}",
                "method": "sub",
                "data": {
                    "mode": "full",
                    "instrumentKeys": batch,
                },
            }
            await self._ws.send_bytes(json.dumps(msg, separators=(",", ":")).encode("utf-8"))
            logger.info(
                "upstox_subscribed_batch",
                batch=i // batch_size + 1,
                count=len(batch),
            )
            await asyncio.sleep(self.config.subscribe_batch_delay_ms / 1000)

        self.state = ConnectionState.STREAMING
        logger.info("upstox_subscribed_all", total=len(instrument_keys))

    async def unsubscribe(self, instrument_keys: list[str]) -> None:
        """Unsubscribe in batches using Upstox V3's "unsub" method.

        EBIE EB-0 infrastructure -- not yet called by any tiering logic.
        """
        if not self._ws:
            raise RuntimeError("Upstox WebSocket is not connected")

        batch_size = self.config.subscribe_batch_size
        for i in range(0, len(instrument_keys), batch_size):
            batch = instrument_keys[i : i + batch_size]
            msg = {
                "guid": f"infusion-unsub-{int(time.time() * 1000)}-{i}",
                "method": "unsub",
                "data": {"instrumentKeys": batch},
            }
            await self._ws.send_bytes(json.dumps(msg, separators=(",", ":")).encode("utf-8"))
            logger.info(
                "upstox_unsubscribed_batch",
                batch=i // batch_size + 1,
                count=len(batch),
            )
            await asyncio.sleep(self.config.subscribe_batch_delay_ms / 1000)

    async def change_mode(self, instrument_keys: list[str], mode: str) -> None:
        """Promote/demote already-subscribed instruments to a different feed
        mode (e.g. "full" -> "full_d30") using Upstox V3's "change_mode"
        method, in batches, same pattern as subscribe()/unsubscribe().

        EBIE EB-0 infrastructure -- not yet called by any tiering logic;
        EB-6 (microstructure) is what will invoke this to promote Tier-3
        candidates to deeper depth when infusion_models.capability's
        supports_full_d30 is True.
        """
        if not self._ws:
            raise RuntimeError("Upstox WebSocket is not connected")

        batch_size = self.config.subscribe_batch_size
        for i in range(0, len(instrument_keys), batch_size):
            batch = instrument_keys[i : i + batch_size]
            msg = {
                "guid": f"infusion-mode-{int(time.time() * 1000)}-{i}",
                "method": "change_mode",
                "data": {"mode": mode, "instrumentKeys": batch},
            }
            await self._ws.send_bytes(json.dumps(msg, separators=(",", ":")).encode("utf-8"))
            logger.info(
                "upstox_mode_changed_batch",
                batch=i // batch_size + 1,
                count=len(batch),
                mode=mode,
            )
            await asyncio.sleep(self.config.subscribe_batch_delay_ms / 1000)

    async def start_streaming(self, on_tick: Callable[[RawTickV1], Awaitable[None]]) -> None:
        """Read WS frames, decode protobuf, invoke callback."""
        assert self._ws is not None
        self.state = ConnectionState.STREAMING
        logger.info("upstox_streaming_started")

        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                received_at = now_us()
                try:
                    frame = msg.data
                    if len(frame) >= 2 and frame[:2] == b"\x1f\x8b":
                        frame = gzip.decompress(frame)

                    decoded_ticks = decode_feed_response(frame)
                    for decoded in decoded_ticks:
                        tick = self._to_raw_tick(decoded, received_at)
                        await on_tick(tick)
                        self._tick_count += 1
                        self._last_tick_time = time.time()

                    if self._tick_count and self._tick_count % 10000 == 0:
                        logger.info("upstox_tick_progress", ticks=self._tick_count)
                except (ProtoDecodeError, OSError, ValueError) as exc:
                    logger.warning(
                        "upstox_tick_decode_error",
                        error=str(exc),
                        frame_len=len(msg.data),
                    )

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("upstox_ws_error", error=str(self._ws.exception()))
                break

            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                logger.warning("upstox_ws_closed", close_code=msg.data)
                break

    def _to_raw_tick(self, decoded: DecodedFeed, received_at_us: int) -> RawTickV1:
        exchange, segment = self._split_instrument_key(decoded.instrument_key)
        return RawTickV1(
            broker="upstox",
            instrument_key=decoded.instrument_key,
            exchange=exchange,
            segment=segment,
            ltp=decoded.ltp,
            open=decoded.open,
            high=decoded.high,
            low=decoded.low,
            close=decoded.close,
            volume=decoded.volume,
            oi=decoded.oi,
            total_buy_qty=decoded.total_buy_qty,
            total_sell_qty=decoded.total_sell_qty,
            best_bid=decoded.best_bid,
            best_ask=decoded.best_ask,
            best_bid_qty=decoded.best_bid_qty,
            best_ask_qty=decoded.best_ask_qty,
            exchange_timestamp_ms=decoded.exchange_timestamp_ms,
            received_at_us=received_at_us,
            depth_levels=decoded.depth_levels or [],
            atp=decoded.atp,
        )

    @staticmethod
    def _split_instrument_key(instrument_key: str) -> tuple[str, str]:
        prefix = instrument_key.split("|", 1)[0]
        if "_" not in prefix:
            return prefix or "NSE", ""
        exchange, segment = prefix.split("_", 1)
        return exchange, segment

    async def disconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED
        self._disconnected_at = time.time()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("upstox_disconnected", total_ticks=self._tick_count)

    def health(self) -> Payload:
        return {
            "broker": self.name,
            "state": self.state.value,
            "auth_error": self._last_auth_error,
            "token_source": self._token_source,
            "token_expiry_ts": self._token_expiry_ts,
            "token_expiry_ist": (
                datetime.fromtimestamp(self._token_expiry_ts, tz=UTC).astimezone(IST).isoformat()
                if self._token_expiry_ts
                else ""
            ),
            "tick_count": self._tick_count,
            "last_tick_age_ms": round((time.time() - self._last_tick_time) * 1000)
            if self._last_tick_time
            else -1,
            "reconnect_count": self._reconnect_count,
            "last_gap_ms": self._last_gap_ms,
            "cumulative_gap_ms": self._cumulative_gap_ms,
        }
