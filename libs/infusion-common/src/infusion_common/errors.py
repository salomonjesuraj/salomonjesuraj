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

    # Upstox broker auth failures -- checked before the generic RETRYABLE
    # fallback so an expired/invalid/missing token is distinguishable from
    # a transient network blip. Matches the exact RuntimeError messages
    # ingestion/adapters/upstox.py's authenticate()/connect() raise (a
    # missing token, an expired token per the JWT's own exp claim, or a
    # 401 from Upstox's /authorize endpoint). Phase 13.2 audit finding:
    # without this, ConnectionSupervisor retried a KNOWN-bad token every
    # ~30s forever (its exponential backoff caps at reconnect_max), which
    # cannot succeed by retrying and only adds pointless request volume
    # against Upstox's own rate limits (UDAPI10005) while waiting for a
    # human to fix the token via the dashboard or the login flow.
    if isinstance(exception, RuntimeError) and (
        "Upstox access token" in msg or "Upstox WS auth failed: 401" in msg
    ):
        return InfusionError(
            ErrorCategory.BROKER, ErrorSource.BROKER_WS, msg, exception,
            context={"auth_failure": True},
        )

    # Fatal errors
    if isinstance(exception, (MemoryError, AssertionError)):
        return InfusionError(ErrorCategory.FATAL, ErrorSource.INTERNAL, msg, exception)

    # Data errors
    if isinstance(exception, (ValueError, ZeroDivisionError)):
        return InfusionError(ErrorCategory.MALFORMED_DATA, ErrorSource.INTERNAL, msg, exception)

    # Default: retryable
    return InfusionError(ErrorCategory.RETRYABLE, ErrorSource.INTERNAL, msg, exception)
