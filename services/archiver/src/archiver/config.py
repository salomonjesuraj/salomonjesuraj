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
    write_batch_size: int = 10  # batch INSERT threshold
    write_flush_sec: float = 5.0  # max seconds before forced flush

    # Outcome tracker
    # EBIE EB-10A: widened from 5 min (real finding: at the old 5-min TTL,
    # 100% of archived TARGET_HIT/STOP_HIT resolutions happened within
    # 0-5.5 minutes of firing, confirmed via direct query -- meaning NONE
    # of the blueprint's proposed calibration horizons (30/45/60min
    # intraday) were ever reachable by this tracker at all; every signal
    # that didn't resolve almost immediately was simply given up on as
    # EXPIRED well before those horizons. Widened to comfortably cover
    # the 60-min max intraday horizon with margin -- this ONLY affects
    # how long the background outcome-tracking research process keeps
    # sampling LTP for an already-fired signal before giving up; it does
    # NOT touch scanner's own signal_ttl_sec (a completely separate
    # config in a separate service, governing live suppression/cooldown/
    # active-signal display), so no live alerting/dedup behavior changes.
    # Swing-horizon (1-3 session) calibration would need a much longer
    # TTL plus cross-session-boundary handling in _get_ltp/tracker.py --
    # explicitly out of scope for this change, a further follow-up.
    tracker_interval_sec: int = 30  # outcome sampling interval
    tracker_lookback_min: int = (
        90  # track signals from last N minutes (must exceed signal_ttl_min or a
    )
    # still-open signal would fall out of the query before reaching its TTL)
    signal_ttl_min: int = 75  # signal validity window for outcome tracking (see note above)

    # Market hours (IST) — only track outcomes during market
    market_open_hour: int = 9
    market_open_min: int = 15
    market_close_hour: int = 15
    market_close_min: int = 30

    # Backfill
    backfill_on_startup: bool = True  # replay existing stream history
    backfill_batch_size: int = 100  # messages per XRANGE batch
