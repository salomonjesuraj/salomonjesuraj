"""Service lifecycle management — startup, shutdown, signal handling."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable

import structlog

logger = structlog.get_logger()


class ServiceLifecycle:
    """Manages graceful startup and shutdown for async services."""

    def __init__(self, service_name: str):
        self.service_name = service_name
        self._shutdown_event = asyncio.Event()
        self._cleanup_callbacks: list[Callable[[], Awaitable]] = []

    @property
    def should_run(self) -> bool:
        """True while service should continue processing."""
        return not self._shutdown_event.is_set()

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._shutdown_event

    def register_cleanup(self, callback: Callable) -> None:
        """Register a cleanup callback for graceful shutdown.

        Accepts both async and sync callables (sync will be awaited as-is
        if they return a coroutine, otherwise called directly).
        """
        self._cleanup_callbacks.append(callback)

    # Keep backward compat alias
    on_shutdown = register_cleanup

    def install_signal_handlers(self) -> None:
        """Install SIGTERM/SIGINT handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        def _handle_signal(sig: signal.Signals) -> None:
            logger.info(
                "shutdown_signal_received",
                service=self.service_name,
                signal=sig.name,
            )
            self._shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            # Windows does not support add_signal_handler.
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _handle_signal, sig)

    async def run_until_shutdown(self, main_task: Callable[[], Awaitable]) -> None:
        """Run the main task until shutdown is requested."""
        self.install_signal_handlers()
        logger.info("service_starting", service=self.service_name)

        task = asyncio.create_task(main_task())

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        logger.info("service_shutting_down", service=self.service_name)

        # Cancel main task
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Run cleanup callbacks
        await self.cleanup()

    async def cleanup(self):
        """Run all registered cleanup tasks."""
        logger.info("cleanup_start", service=self.service_name)
        for cb in reversed(self._cleanup_callbacks):
            try:
                result = cb()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(
                    "cleanup_failed",
                    service=self.service_name,
                    error=str(e),
                )
        logger.info("service_stopped", service=self.service_name)
