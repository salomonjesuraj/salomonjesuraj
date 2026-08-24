"""Historical MTF cache warmup worker for Phase 4."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime
from typing import Any

import structlog

from api.option_chain_queue import build_candidates
from api.routes.mtf import compute_mtf

logger = structlog.get_logger()

STATUS_KEY = "infusion:mtf-queue:status"
Payload = dict[str, Any]


async def refresh_mtf_candidates_once(redis: Any, *, limit: int = 60) -> Payload:
    started = datetime.now().isoformat(timespec="seconds")
    candidates = await build_candidates(redis, limit)
    refreshed: list[str] = []
    failed: list[Payload] = []
    for row in candidates:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        try:
            await compute_mtf(redis, symbol, store=True)
            refreshed.append(symbol)
        except Exception as exc:
            failed.append({"symbol": symbol, "error": str(exc)[:160]})
            logger.warning("mtf_queue_refresh_failed", symbol=symbol, error=str(exc))
    status = {
        "enabled": True,
        "started_at": started,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_limit": limit,
        "candidate_count": len(candidates),
        "refreshed_count": len(refreshed),
        "failed_count": len(failed),
        "refreshed": refreshed[:80],
        "failed": failed[:20],
    }
    await redis.set(STATUS_KEY, json.dumps(status, separators=(",", ":")), ex=600)
    return status


async def mtf_queue_loop(app: Any) -> None:
    config = app["config"]
    redis = app["redis"]
    enabled = bool(getattr(config, "mtf_auto_refresh_enabled", True))
    interval = max(45, int(getattr(config, "mtf_refresh_interval_sec", 90) or 90))
    limit = max(0, int(getattr(config, "mtf_candidate_limit", 60) or 60))
    if not enabled or limit <= 0:
        await redis.set(
            STATUS_KEY, json.dumps({"enabled": False, "reason": "disabled_by_config"}), ex=600
        )
        return
    logger.info("mtf_queue_started", interval=interval, limit=limit)
    while True:
        with contextlib.suppress(Exception):
            status = await refresh_mtf_candidates_once(redis, limit=limit)
            logger.info(
                "mtf_queue_cycle", **{k: v for k, v in status.items() if not isinstance(v, list)}
            )
        await asyncio.sleep(interval)
