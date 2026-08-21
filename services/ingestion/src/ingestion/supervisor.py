"""Connection supervisor — manages adapter lifecycle with automatic reconnection.

State machine:
  INIT -> AUTHENTICATING -> CONNECTING -> SUBSCRIBING -> STREAMING
                                                            |
                                                            v (on disconnect/error)
                                                         RECONNECTING
                                                            |
                                                            v (backoff)
                                                         CONNECTING -> ...
"""

import asyncio
import contextlib
import random

import structlog
from infusion_common.errors import ErrorCategory, classify_error

from ingestion.adapters.base import BrokerAdapter, ConnectionState

logger = structlog.get_logger()


class ConnectionSupervisor:
    """Manages adapter lifecycle with automatic reconnection."""

    def __init__(
        self,
        adapter: BrokerAdapter,
        instruments: list[str],
        on_tick,
        redis=None,
        reconnect_base: float = 1.0,
        reconnect_max: float = 30.0,
        jitter_pct: float = 0.20,
        auth_failure_backoff_sec: float = 300.0,
    ):
        self.adapter = adapter
        self.instruments = instruments
        self.on_tick = on_tick
        self.redis = redis
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.jitter_pct = jitter_pct
        # Phase 13.2 audit fix: a token failure will not self-resolve by
        # retrying sooner, so it gets a much longer, flat wait instead of
        # the exponential ramp meant for transient network blips -- see
        # classify_error()'s auth_failure branch in infusion_common.errors.
        # _sleep_or_force_recheck() still wakes this early the instant a
        # human saves a fresh token, so this doesn't add real recovery
        # latency, only stops uselessly hammering Upstox's /authorize
        # endpoint while nothing has changed.
        self.auth_failure_backoff_sec = auth_failure_backoff_sec
        self._consecutive_failures = 0
        self._last_error_was_auth_failure = False
        self._running = True

    async def run(self):
        """Main supervisor loop. Runs until stopped."""
        while self._running:
            try:
                await self.adapter.authenticate()
                await self.adapter.connect()
                await self.adapter.subscribe(self.instruments)
                self._consecutive_failures = 0

                # This blocks until WS disconnects
                await self.adapter.start_streaming(self.on_tick)

                # If we reach here, WS closed normally -- reset the auth-
                # failure flag so a stale True from an EARLIER iteration
                # can't wrongly apply the long backoff to this unrelated
                # normal close.
                self._last_error_was_auth_failure = False
                logger.warning("ws_session_ended")

            except Exception as e:
                err = classify_error(e)
                logger.error("supervisor_error", **err.to_log_dict())

                if err.category == ErrorCategory.FATAL:
                    logger.critical("fatal_error_stopping", error=str(e))
                    raise

                self._last_error_was_auth_failure = bool(
                    err.context and err.context.get("auth_failure")
                )

            finally:
                with contextlib.suppress(Exception):
                    await self.adapter.disconnect()

            if not self._running:
                break

            self._consecutive_failures += 1
            if self._last_error_was_auth_failure:
                delay = self.auth_failure_backoff_sec
                logger.info(
                    "reconnect_deferred_auth_failure",
                    attempt=self._consecutive_failures,
                    delay_sec=delay,
                )
            else:
                delay = self._calculate_backoff()
                logger.info(
                    "reconnecting",
                    attempt=self._consecutive_failures,
                    delay_sec=round(delay, 2),
                )
            await self._sleep_or_force_recheck(delay)

    def _calculate_backoff(self) -> float:
        """Exponential backoff with jitter."""
        delay = min(
            self.reconnect_base * (2 ** (self._consecutive_failures - 1)),
            self.reconnect_max,
        )
        jitter = delay * self.jitter_pct * (2 * random.random() - 1)
        return max(0.1, delay + jitter)

    async def _sleep_or_force_recheck(self, delay: float) -> None:
        """Sleep in small slices so a dashboard token save can wake us now."""
        if not self.redis:
            await asyncio.sleep(delay)
            return

        deadline = asyncio.get_running_loop().time() + delay
        while self._running and asyncio.get_running_loop().time() < deadline:
            try:
                flag = await self.redis.get("infusion:auth:upstox:force_recheck")
                if flag:
                    await self.redis.delete("infusion:auth:upstox:force_recheck")
                    self._consecutive_failures = 0
                    logger.info("force_recheck_received")
                    return
            except Exception as exc:
                logger.warning("force_recheck_poll_failed", error=str(exc))
                await asyncio.sleep(min(delay, 1.0))
                return
            await asyncio.sleep(min(1.0, max(0.1, deadline - asyncio.get_running_loop().time())))

    def stop(self):
        self._running = False
        self.adapter.state = ConnectionState.DISCONNECTED
