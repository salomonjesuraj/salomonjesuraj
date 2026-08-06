"""NSE Scraper service configuration."""

from infusion_common.config import InfusionSettings


class NseScraperSettings(InfusionSettings):
    """NSE scraper settings — all overridable via INFUSION_* env vars."""

    service_name: str = "nse-scraper"

    # Symbol universe tier: nifty50 | nifty100 | nifty200 | nifty500 | fno | custom
    symbol_universe: str = "nifty200"

    # Config directory where nifty50.json etc. live
    symbols_config_dir: str = "/app/config/symbols"

    # OAuth callback for broker token refresh
    oauth_callback_port: int = 5100
    oauth_callback_host: str = "0.0.0.0"

    # Upstox — for OAuth token exchange
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = "http://localhost:5100/callback/upstox"

    # Refresh interval: how often to re-check symbol state (seconds)
    symbol_refresh_interval_sec: int = 3600  # 1 hour
