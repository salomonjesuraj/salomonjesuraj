"""Structured logging configuration using structlog."""

import logging
import sys
from collections.abc import Callable
from typing import Any

import structlog

_SECRET_SUBSTRINGS = ("token", "secret", "password", "key", "credential")


def _strip_secrets(
    logger: logging.Logger, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Remove fields that look like secrets."""
    for field_name in list(event_dict.keys()):
        if any(s in field_name.lower() for s in _SECRET_SUBSTRINGS):
            event_dict[field_name] = "***REDACTED***"
    return event_dict


def _add_service_name(
    service_name: str,
) -> Callable[[logging.Logger, str, dict[str, Any]], dict[str, Any]]:
    """Processor factory that adds service name to every log."""

    def processor(
        logger: logging.Logger, method_name: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        event_dict["service"] = service_name
        return event_dict

    return processor


def setup_logging(
    service_name: str,
    level: str = "info",
    fmt: str = "json",
) -> None:
    """Configure structlog for all Infusion services."""

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service_name(service_name),
        _strip_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "console":
        renderer: Any = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
