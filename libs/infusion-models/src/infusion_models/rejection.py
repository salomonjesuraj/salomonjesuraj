"""Unified rejection-code taxonomy — pipeline audit fix B5 (2026-08-25).

Before this, "why was this candidate rejected" lived in two separate,
loosely-typed vocabularies: scanner/suppression.py's short reason
strings (e.g. "sector_weak", "low_conviction") and
api/routes/market.py's free-form human sentences in `hard_blockers`
(e.g. "Wide spread 7.2%"). Neither was a stable code a dashboard could
slice/count by without string-matching text that can change wording at
any time.

This does NOT replace either existing string field -- both still carry
the human-readable detail exactly as before. Consumers that want a
stable, enumerable reason attach a `RejectionCode` alongside it. Not
every existing rejection has (or needs) a matching code yet; only
suppression/rejection paths that were explicitly re-audited carry one
so far -- an uncoded rejection is a disclosed gap, not a bug.
"""

from enum import StrEnum


class RejectionCode(StrEnum):
    """Stable, dashboard-sliceable rejection reason. Values are the
    literal strings persisted to Postgres and returned over the API --
    treat renaming a member as a breaking schema change, not a refactor."""

    REJECTED_OI_WALL = "REJECTED_OI_WALL"
    REJECTED_OPTION_SPREAD = "REJECTED_OPTION_SPREAD"
    REJECTED_IV_CRUSH = "REJECTED_IV_CRUSH"
    REJECTED_INDEX_DIVERGENCE = "REJECTED_INDEX_DIVERGENCE"
    REJECTED_LIQUIDITY = "REJECTED_LIQUIDITY"
    REJECTED_DELTA_BAND = "REJECTED_DELTA_BAND"
    REJECTED_THETA_CUTOFF = "REJECTED_THETA_CUTOFF"
    REJECTED_LOW_CONVICTION = "REJECTED_LOW_CONVICTION"
    REJECTED_SECTOR_WEAK = "REJECTED_SECTOR_WEAK"
