"""WS gateway configuration."""

from infusion_common.config import InfusionSettings


class WSGatewaySettings(InfusionSettings):
    service_name: str = "ws-gateway"

    # Server
    ws_host: str = "0.0.0.0"
    ws_port: int = 8001

    # Batching
    price_batch_ms: int = 100          # batch price updates every 100ms
    signal_immediate: bool = True      # push signals immediately

    # Consumer
    consumer_batch_size: int = 100
    consumer_block_ms: int = 5
