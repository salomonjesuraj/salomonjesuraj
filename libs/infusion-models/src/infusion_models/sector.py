"""Sector state models."""

from pydantic import BaseModel


class SectorStateV1(BaseModel, frozen=True):
    """Sector-level aggregated state."""

    sector_id: str
    breadth: float = 0.0
    pct_above_vwap: float = 0.0
    weighted_return_pct: float = 0.0
    money_flow_score: float = 0.0
    rotation_score: float = 0.0
    rotation_quadrant: str = ""
    advance_count: int = 0
    decline_count: int = 0
