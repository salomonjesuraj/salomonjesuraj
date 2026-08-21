"""Shared enumerations used across the Infusion system."""

from enum import StrEnum


class Exchange(StrEnum):
    NSE = "NSE"
    BSE = "BSE"


class Segment(StrEnum):
    EQUITY = "EQUITY"
    FNO = "FNO"
    INDEX = "INDEX"


class Series(StrEnum):
    EQ = "EQ"
    BE = "BE"
    BL = "BL"


class SignalType(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class ConvictionGrade(StrEnum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class InstrumentTier(StrEnum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"


class SignalLifecycle(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    SUPPRESSED = "suppressed"


class MarketRegime(StrEnum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"


class PreBreakoutState(StrEnum):
    IDLE = "idle"
    COMPRESSING = "compressing"
    ACCUMULATING = "accumulating"
    COILED = "coiled"
    TRIGGERED = "triggered"
