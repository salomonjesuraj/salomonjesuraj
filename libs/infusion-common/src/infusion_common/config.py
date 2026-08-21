"""Base configuration for all Infusion services."""

from __future__ import annotations

import uuid

from pydantic_settings import BaseSettings


class InfusionSettings(BaseSettings):
    """Base settings inherited by every Infusion service."""

    # Service identity
    service_name: str = "unknown"
    instance_id: str = ""
    environment: str = "development"  # development | staging | production

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # PostgreSQL
    database_url: str = ""

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json | console

    # Health
    health_interval_sec: int = 10
    health_ttl_sec: int = 30

    model_config = {
        "env_prefix": "INFUSION_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }

    def model_post_init(self, __context) -> None:
        if not self.instance_id:
            self.instance_id = f"{self.service_name}-{uuid.uuid4().hex[:8]}"
