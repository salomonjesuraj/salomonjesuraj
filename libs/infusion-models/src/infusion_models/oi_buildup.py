"""OI buildup taxonomy — the classic 4-quadrant (price direction x OI
direction) classification, filling audit finding §3.1 ("genuinely not
implemented anywhere") from the 2026-08-25 mathematical audit.

    price UP   + OI UP   -> LONG_BUILDUP    (aggressive buyers entering)
    price UP   + OI DOWN -> SHORT_COVERING  (shorts closing out)
    price DOWN + OI UP   -> SHORT_BUILDUP   (aggressive sellers entering)
    price DOWN + OI DOWN -> LONG_UNWINDING  (longs closing out)

NEUTRAL covers every case that doesn't clear the classification
deadband on both axes (see api/futures.py's classify_oi_buildup) --
never forced into one of the four quadrants on noise.
"""

from enum import StrEnum


class OIBuildupType(StrEnum):
    LONG_BUILDUP = "LONG_BUILDUP"
    SHORT_COVERING = "SHORT_COVERING"
    SHORT_BUILDUP = "SHORT_BUILDUP"
    LONG_UNWINDING = "LONG_UNWINDING"
    NEUTRAL = "NEUTRAL"
