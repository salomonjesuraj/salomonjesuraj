"""Archiver service configuration."""

from infusion_common.config import InfusionSettings


class ArchiverSettings(InfusionSettings):
    # Database
    database_url: str = "postgresql://infusion:changeme@localhost:5432/infusion"

    # Consumer
    archiver_consumer_name: str = "archiver-0"
    archiver_batch_size: int = 10
    archiver_block_ms: int = 500

    # Writer
    write_batch_size: int = 10         # batch INSERT threshold
    write_flush_sec: float = 5.0       # max seconds before forced flush

    # Outcome tracker
    tracker_interval_sec: int = 30     # outcome sampling interval
    tracker_lookback_min: int = 60     # track signals from last N minutes
    signal_ttl_min: int = 5            # signal validity window for outcome

    # Market hours (IST) — only track outcomes during market
    market_open_hour: int = 9
    market_open_min: int = 15
    market_close_hour: int = 15
    market_close_min: int = 30

    # Backfill
    backfill_on_startup: bool = True   # replay existing stream history
    backfill_batch_size: int = 100     # messages per XRANGE batch
