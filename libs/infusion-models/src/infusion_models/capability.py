"""Provider (broker) capability registry — EBIE EB-0.

A static, operator-declared entitlement record for a market-data provider.
Deliberately NOT runtime-probed: Upstox's public API exposes no "what plan
is this account on" endpoint, so there is nothing to query. This record is
populated from config by whoever has actually checked their own Upstox
account/commercial terms, and published to Redis so every consumer (scanner,
api, dashboard, and later EBIE tiering logic) reads one canonical answer
instead of each guessing independently from source code.

See docs/EBIE-IMPLEMENTATION-QUESTIONS.md Q2.1/Q7.1 and the authorized
answers doc's Section 2/7 for the full reasoning and the verified public
Upstox V3 mode names this maps to ("full" = 5-level depth = supports_full_d5;
"full_d30" = Plus-only = supports_full_d30).
"""

from pydantic import BaseModel


class ProviderCapabilityV1(BaseModel, frozen=True):
    """What a market-data provider account is actually entitled to.

    Defaults are the conservative, always-documented "Normal" Upstox V3
    tier baseline. Plus-only capabilities (full_d30) default False until
    an operator who has checked their own account explicitly enables them
    via config -- never auto-assumed, never inferred from source code.
    """

    provider: str = "upstox"
    ws_connections_available: int = 2
    supports_full_d5: bool = True
    supports_full_d30: bool = False
    supports_option_greeks: bool = True
    supports_news: bool = True
    source: str = "static_config"  # "static_config" | "verified_probe"
    checked_at_us: int = 0
