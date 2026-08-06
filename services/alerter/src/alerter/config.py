"""Alerter service configuration.

Extends InfusionSettings with alerter-specific parameters:
  - Telegram bot credentials
  - Consumer group tuning
  - Delivery priority and cooldown
  - Rate limiting and burst protection
  - Retry policy
  - Operational modes (dry_run, watchlist_only)
"""

from infusion_common.config import InfusionSettings


class AlerterSettings(InfusionSettings):
    """Alerter-specific settings. All overridable via environment variables."""

    # ─── Telegram ──────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ─── Consumer ──────────────────────────────────
    alerter_consumer_name: str = "alerter-0"
    alerter_batch_size: int = 10
    alerter_block_ms: int = 500

    # ─── Priority: minimum grade for delivery ──────
    alert_min_grade: str = "B+"  # A+, A, B+ delivered; B, C, D passive

    # ─── Delivery cooldown (per-symbol, separate from scanner cooldown) ──
    alert_cooldown_sec: int = 1800  # 30 min

    # ─── Rate limiting ─────────────────────────────
    global_rate_limit: int = 10     # max alerts per hour
    burst_limit: int = 3            # max alerts per 5-min window

    # ─── Retry ─────────────────────────────────────
    retry_max: int = 3
    retry_base_sec: float = 2.0     # exponential backoff: 2, 4, 8

    # ─── Modes ─────────────────────────────────────
    dry_run: bool = False           # log messages but don't send
    watchlist_only: bool = False    # only send pre-breakout summaries

    # ─── Delivery log ──────────────────────────────
    delivery_log_max: int = 100     # max entries in delivery log list
