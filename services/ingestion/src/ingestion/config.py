"""Ingestion service configuration."""

from infusion_common.config import InfusionSettings


class IngestionSettings(InfusionSettings):
    service_name: str = "ingestion"

    # Broker
    broker_primary: str = "upstox"         # "upstox" | "mock"
    broker_secondary: str = ""

    # Upstox
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = "http://localhost:5100/callback/upstox"
    upstox_access_token: str = ""          # set via env or Redis

    # Mock adapter
    mock_symbols: int = 5                  # number of symbols to simulate
    mock_tick_rate_hz: int = 10            # ticks per second

    # Connection
    ws_ping_interval_sec: int = 30
    ws_ping_timeout_sec: int = 10
    reconnect_base_sec: float = 1.0
    reconnect_max_sec: float = 30.0
    reconnect_jitter_pct: float = 0.20

    # Subscription
    subscribe_batch_size: int = 100
    subscribe_batch_delay_ms: int = 100
