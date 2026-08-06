"""Alert models."""

from pydantic import BaseModel


class AlertOutboundV1(BaseModel, frozen=True):
    """Outbound alert for delivery to Telegram/WS."""

    symbol: str
    strategy: str
    signal_type: str
    conviction_grade: str = ""
    conviction_score: float = 0.0
    price_at_signal: float = 0.0
    headline: str = ""
    body: str = ""
    channel: str = "websocket"
