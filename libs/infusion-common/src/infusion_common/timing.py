"""Latency measurement utilities."""

import time
from functools import wraps

import structlog

logger = structlog.get_logger()


def now_us() -> int:
    """Current UTC epoch in microseconds."""
    return int(time.time() * 1_000_000)


def measure_latency(operation: str):
    """Decorator that logs async operation latency in microseconds."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
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
