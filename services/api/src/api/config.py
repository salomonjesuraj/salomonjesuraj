"""API service configuration."""

from infusion_common.config import InfusionSettings


class APISettings(InfusionSettings):
    service_name: str = "api"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # OpenAI is advisory-only. Core scanning never depends on this integration.
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-mini"
    openai_timeout_sec: int = 20
    openai_cache_ttl_sec: int = 300

    # Upstox REST routes: option-chain scoring and index quote fallback.
    upstox_access_token: str = ""

    # Smart option-chain queue.  This avoids brute-force chain calls for every
    # F&O symbol while keeping top/selected candidates contract-confirmed.
    option_chain_auto_refresh_enabled: bool = True
    option_chain_refresh_interval_sec: int = 45
    option_chain_candidate_limit: int = 28
    option_chain_request_delay_ms: int = 350

    # Historical MTF cache warmer for Phase 4.
    mtf_auto_refresh_enabled: bool = True
    mtf_refresh_interval_sec: int = 90
    mtf_candidate_limit: int = 60
