"""Stream names, consumer groups, key prefixes, and limits.

Single source of truth — no hardcoded stream names anywhere else.
"""

# ═══════════════════════════════════════════════════
# Primary Streams
# ═══════════════════════════════════════════════════
STREAM_TICK_RAW = "infusion:stream:tick:raw"
STREAM_TICK_NORMALIZED = "infusion:stream:tick:normalized"
STREAM_FEATURE_COMPUTED = "infusion:stream:feature:computed"
STREAM_SCAN_SIGNALS = "infusion:stream:scan:signals"
STREAM_SCAN_SUPPRESSED = "infusion:stream:scan:suppressed"
STREAM_SECTOR_STATE = "infusion:stream:sector:state"
STREAM_CONVICTION_RANKED = "infusion:stream:conviction:ranked"
STREAM_RECAP = "infusion:stream:recap"

# ═══════════════════════════════════════════════════
# Consumer Groups
# ═══════════════════════════════════════════════════
CG_NORMALIZER = "normalizer-cg"
CG_FEATURE = "feature-cg"
CG_SCANNER = "scanner-cg"
CG_SECTOR = "sector-cg"
CG_CONVICTION = "conviction-cg"
CG_ALERT = "alert-cg"
CG_ARCHIVER = "archiver-cg"
CG_ARCHIVER_SUP = "archiver-sup-cg"
CG_DASHBOARD = "dashboard-cg"

# ═══════════════════════════════════════════════════
# DLQ Streams
# ═══════════════════════════════════════════════════
DLQ_PREFIX = "infusion:dlq:"

# ═══════════════════════════════════════════════════
# MAXLEN Limits (approximate)
# ═══════════════════════════════════════════════════
MAXLEN_TICK_RAW = 100_000
MAXLEN_TICK_NORMALIZED = 200_000
MAXLEN_FEATURE_COMPUTED = 100_000
MAXLEN_SIGNALS = 10_000
MAXLEN_SCAN_SUPPRESSED = 5_000
MAXLEN_SECTOR_STATE = 10_000
MAXLEN_CONVICTION = 10_000
MAXLEN_RECAP = 500
MAXLEN_DLQ = 1_000

# ═══════════════════════════════════════════════════
# Hot State Keys
# ═══════════════════════════════════════════════════
KEY_TICK_PREFIX = "infusion:tick:"         # + {symbol}  → HASH
KEY_FEATURE_PREFIX = "infusion:feature:"   # + {symbol}  → HASH
KEY_OHLC_PREFIX = "infusion:ohlc:"        # + {symbol}:{tf} → ZSET
KEY_HEALTH_PREFIX = "infusion:health:"     # + {service} → STRING
KEY_SYMBOLS = "infusion:symbols"           # HASH: instrument_key → symbol metadata
KEY_AUTH_UPSTOX = "infusion:auth:upstox"   # STRING: access_token

# Scanner hot state
KEY_SIGNAL_PREFIX = "infusion:signal:"              # + {symbol} → HASH (latest signal)
KEY_SIGNAL_ACTIVE = "infusion:signals:active"       # ZSET: signal_id scored by conviction
KEY_COOLDOWN_PREFIX = "infusion:cooldown:"          # + {symbol}:{strategy_id} → STRING (TTL)
KEY_PRE_BREAKOUT_PREFIX = "infusion:prebreak:"      # + {symbol} → HASH (state machine)
KEY_SECTOR_PREFIX = "infusion:sector:"              # + {sector_id} → HASH

# Alerter hot state
KEY_ALERT_COOLDOWN_PREFIX = "infusion:alert:cooldown:"   # + {symbol} → STRING (TTL)
KEY_ALERT_RATE = "infusion:alert:rate"                   # STRING (counter, TTL=3600)
KEY_ALERT_BURST = "infusion:alert:burst"                 # STRING (counter, TTL=300)
KEY_ALERT_LOG = "infusion:alert:log"                     # LIST (capped, last 100 deliveries)
KEY_ALERT_DELIVERED = "infusion:alert:delivered"          # SET of delivered signal_ids (TTL)
KEY_ALERT_MUTE_SYMBOLS = "infusion:alert:mute:symbols"   # SET of muted symbols
KEY_ALERT_MUTE_STRATEGIES = "infusion:alert:mute:strat"  # SET of muted strategies

# Archiver hot state
KEY_ARCHIVER_CHECKPOINT = "infusion:archiver:checkpoint"  # HASH: stream → last_id

# EBIE EB-0: provider capability registry + dynamic subscription state
KEY_CAPABILITY_PREFIX = "infusion:capability:"      # + {provider} → STRING (msgpack ProviderCapabilityV1)
KEY_SUBSCRIPTION_STATUS = "infusion:subscription:status"   # STRING (msgpack: tier counts + reconnect/gap)
KEY_SUBSCRIPTION_TIER_PREFIX = "infusion:subtier:"  # + {instrument_key} → HASH {tier, mode, updated_at}

# EBIE EB-1: canonical state machine (shadow mode) -- previous-state cache,
# same "cheap bulk MGET, TTL so it ages out" shape as R2/R8/R9's own
# transitional caches (infusion:vwap-state:, infusion:opening-range:,
# infusion:radar-alert-tier:).
KEY_EBIE_STATE_PREFIX = "infusion:ebie-state:"      # + {symbol}:{direction} → STRING (current EBIE state)

# EBIE EB-15 Phase 3: lightweight universe-wide verdict (one per symbol,
# every sweep) -- same single-key msgpack-blob STRING shape as
# infusion:mtf:{symbol}/infusion:sentiment:{symbol}, since this carries a
# small structured dict (verdict/confidence/reasons/invalidation/DQ), not
# a plain scalar like KEY_EBIE_STATE_PREFIX above.
KEY_EBIE_VERDICT_LITE_PREFIX = "infusion:ebie-verdict-lite:"  # + {symbol} → STRING (msgpack'd lightweight verdict dict)

