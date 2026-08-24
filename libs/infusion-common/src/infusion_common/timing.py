"""Latency measurement utilities."""

import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

import structlog

logger = structlog.get_logger()
T = TypeVar("T")


def now_us() -> int:
    """Current UTC epoch in microseconds."""
    return int(time.time() * 1_000_000)


def measure_latency(
    operation: str,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator that logs async operation latency in microseconds."""

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            start = time.perf_counter_ns()
            result = await func(*args, **kwargs)
            elapsed_us = (time.perf_counter_ns() - start) / 1000
            logger.debug(
                "latency",
                operation=operation,
                latency_us=round(elapsed_us, 1),
            )
            return result

        return wrapper

    return decorator
