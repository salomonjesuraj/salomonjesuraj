"""Structured error taxonomy for the Infusion system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    RETRYABLE = "retryable"
    FATAL = "fatal"
    MALFORMED_DATA = "malformed_data"
    INFRASTRUCTURE = "infrastructure"
    BROKER = "broker"
    DOWNSTREAM_OVERLOAD = "downstream_overload"


class ErrorSource(StrEnum):
    REDIS = "redis"
    POSTGRES = "postgres"
    BROKER_WS = "broker_ws"
    BROKER_API = "broker_api"
    NSE = "nse"
    TELEGRAM = "telegram"
    GEMINI = "gemini"
    INTERNAL = "internal"
    CODEC = "codec"
    VALIDATION = "validation"


@dataclass(frozen=True, slots=True)
class InfusionError:
    """Structured error for consistent logging and handling."""

    category: ErrorCategory
    source: ErrorSource
    message: str
    original_exception: Exception | None = None
    context: dict | None = field(default=None)

    def to_log_dict(self) -> dict:
        """Fields to include in structured log output."""
        d: dict = {
            "error_category": self.category.value,
            "error_source": self.source.value,
            "error_message": self.message,
        }
        if self.context:
            d["error_context"] = self.context
        if self.original_exception:
            d["error_type"] = type(self.original_exception).__name__
        return d


def classify_error(exception: Exception) -> InfusionError:
    """Classify an exception into the Infusion error taxonomy."""
    exc_type = type(exception).__name__
    msg = str(exception)

    # Redis errors
    if "redis" in exc_type.lower() or "Redis" in exc_type:
        if "timeout" in msg.lower():
            return InfusionError(ErrorCategory.TRANSIENT, ErrorSource.REDIS, msg, exception)
        if "OOM" in msg:
            return InfusionError(ErrorCategory.FATAL, ErrorSource.REDIS, msg, exception)
        if "BUSYGROUP" in msg:
            return InfusionError(ErrorCategory.TRANSIENT, ErrorSource.REDIS, msg, exception)
        return InfusionError(ErrorCategory.INFRASTRUCTURE, ErrorSource.REDIS, msg, exception)

    # Postgres errors
    if "asyncpg" in exc_type.lower() or "Postgres" in exc_type:
        if "too many" in msg.lower():
            return InfusionError(ErrorCategory.TRANSIENT, ErrorSource.POSTGRES, msg, exception)
        if "data" in exc_type.lower():
            return InfusionError(ErrorCategory.MALFORMED_DATA, ErrorSource.POSTGRES, msg, exception)
        return InfusionError(ErrorCategory.INFRASTRUCTURE, ErrorSource.POSTGRES, msg, exception)

    # Validation errors
    if "ValidationError" in exc_type:
        return InfusionError(ErrorCategory.MALFORMED_DATA, ErrorSource.VALIDATION, msg, exception)

    # Fatal errors
    if isinstance(exception, (MemoryError, AssertionError)):
        return InfusionError(ErrorCategory.FATAL, ErrorSource.INTERNAL, msg, exception)

    # Data errors
    if isinstance(exception, (ValueError, ZeroDivisionError)):
        return InfusionError(ErrorCategory.MALFORMED_DATA, ErrorSource.INTERNAL, msg, exception)

    # Default: retryable
    return InfusionError(ErrorCategory.RETRYABLE, ErrorSource.INTERNAL, msg, exception)
