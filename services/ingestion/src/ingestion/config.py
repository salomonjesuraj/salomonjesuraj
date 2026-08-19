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

    # EBIE EB-0: provider capability -- static, operator-declared Upstox
    # entitlement. Upstox exposes no "what plan am I on" API, so this is
    # set by whoever has actually checked their own Upstox account/
    # commercial terms (see docs/EBIE-IMPLEMENTATION-QUESTIONS.md Q2.1 and
    # the authorized-answers doc's Section 2/7). Defaults are the always-
    # documented "Normal" V3 tier baseline; full_d30 is Plus-only and
    # defaults False until explicitly enabled -- never auto-assumed.
    upstox_ws_connections_available: int = 2
    upstox_supports_full_d30: bool = False
    upstox_supports_news: bool = True
    capability_publish_interval_sec: int = 60
    capability_publish_ttl_sec: int = 180
