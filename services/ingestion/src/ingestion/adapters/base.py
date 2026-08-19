"""Abstract base for broker-specific WebSocket adapters."""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Callable, Awaitable

from infusion_models.tick import RawTickV1


class ConnectionState(StrEnum):
    INIT = "init"
    AUTHENTICATING = "authenticating"
    CONNECTING = "connecting"
    SUBSCRIBING = "subscribing"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class BrokerAdapter(ABC):
    """Abstract base for broker-specific WebSocket adapters."""

    name: str
    state: ConnectionState = ConnectionState.INIT

    @abstractmethod
    async def authenticate(self) -> str:
        """Broker-specific auth. Returns access token."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish WebSocket connection."""
        ...

    @abstractmethod
    async def subscribe(self, instrument_keys: list[str]) -> None:
        """Subscribe to market data for instruments."""
        ...

    @abstractmethod
    async def unsubscribe(self, instrument_keys: list[str]) -> None:
        """Unsubscribe from market data for instruments.

        EBIE EB-0 infrastructure: added so a dynamic subscription registry
        can exist, not because anything calls this yet. Tiered promotion
        logic (EB-6) is what will actually invoke it.
        """
        ...

    @abstractmethod
    async def change_mode(self, instrument_keys: list[str], mode: str) -> None:
        """Promote/demote already-subscribed instruments to a different feed
        mode (e.g. Upstox "full" -> "full_d30") without a full resubscribe.

        EBIE EB-0 infrastructure, same status as unsubscribe() above.
        """
        ...

    @abstractmethod
    async def start_streaming(
        self, on_tick: Callable[[RawTickV1], Awaitable[None]]
    ) -> None:
        """Begin receiving ticks. Calls on_tick for each decoded tick."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Graceful close."""
        ...

    @abstractmethod
    def health(self) -> dict:
        """Return health status dict."""
        ...
