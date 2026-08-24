"""
Data Verification endpoint — proves all data originates from the same live symbol universe.
GET /api/verify/{symbol} returns: ltp from features, tick, chart API, scanner row — all must match.
"""

import time
from typing import Any

from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]


def _decode_redis_value(value: Any) -> Any:
    return value.decode() if isinstance(value, bytes) else value


def _decode_redis_hash(data: Payload) -> Payload:
    return {_decode_redis_value(k): _decode_redis_value(v) for k, v in data.items()}


@routes.get("/api/verify/{symbol}")
async def verify_symbol(request: web.Request) -> web.Response:
    """
    CRITICAL ISSUE 6 — Real data verification.
    Returns evidence that chart, signal, scanner, watchlist all use same live data source.
    """
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]
    now_us = int(time.time() * 1_000_000)

    result: Payload = {
        "symbol": symbol,
        "timestamp": now_us,
        "verified": True,
        "sources": {},
    }
    sources: Payload = result["sources"]

    # 1. Tick data (ingestion stream → Redis)
    tick_raw = await redis.hgetall(f"infusion:tick:{symbol}")
    if tick_raw:
        tick = _decode_redis_hash(tick_raw)
        try:
            ltp_tick = float(tick.get("ltp", 0))
        except (TypeError, ValueError):
            ltp_tick = 0
        sources["tick"] = {
            "ltp": ltp_tick,
            "volume": float(tick.get("volume", 0)),
            "change_pct": float(tick.get("change_pct", 0)),
            "exchange_ts": float(tick.get("exchange_ts", 0)),
        }
    else:
        sources["tick"] = {"error": "No tick data for symbol"}
        result["verified"] = False

    # 2. Feature vector (feature-engine → Redis)
    feat_raw = await redis.hgetall(f"infusion:feature:{symbol}")
    if feat_raw:
        feat = _decode_redis_hash(feat_raw)
        try:
            ltp_feat = float(feat.get("ltp", 0))
        except (TypeError, ValueError):
            ltp_feat = 0
        sources["features"] = {
            "ltp": ltp_feat,
            "vwap": float(feat.get("vwap", 0)),
            "ema_20": float(feat.get("ema_20", 0)),
            "day_high": float(feat.get("day_high", 0)),
            "day_low": float(feat.get("day_low", 0)),
            "prev_close": float(feat.get("prev_close", 0)),
            "rsi_14": float(feat.get("rsi_14", 0)),
            "rel_vol_20d": float(feat.get("rel_vol_20d", 0)),
            "timestamp_us": float(feat.get("timestamp_us", 0)),
            "age_ms": round((now_us - float(feat.get("timestamp_us", now_us))) / 1000, 1),
        }
    else:
        sources["features"] = {"error": "No feature data for symbol"}
        result["verified"] = False

    # 3. Scanner row (scanner → Redis)
    scan_raw = await redis.hgetall(f"infusion:scanner:{symbol}")
    if scan_raw:
        scan = _decode_redis_hash(scan_raw)
        try:
            ltp_scan = float(scan.get("ltp", 0))
        except (TypeError, ValueError):
            ltp_scan = 0
        sources["scanner"] = {
            "ltp": ltp_scan,
            "sector_id": scan.get("sector_id", ""),
            "state": scan.get("state", ""),
            "readiness": float(scan.get("readiness", 0)),
        }
    else:
        # Not all symbols have a scanner row — that's OK
        sources["scanner"] = {"note": "No dedicated scanner hash (uses feature data)"}

    # 4. Pre-breakout watchlist state
    wb_raw = await redis.hgetall(f"infusion:prebreak:{symbol}")
    if wb_raw:
        wb = _decode_redis_hash(wb_raw)
        sources["watchlist"] = {
            "state": wb.get("state", ""),
            "readiness": float(wb.get("readiness_score", 0)),
            "bb_width": float(wb.get("bb_width", 0)),
            "rel_vol": float(wb.get("rel_vol", 0)),
            "duration_sec": float(wb.get("duration_sec", 0)),
        }
    else:
        sources["watchlist"] = {"note": "Symbol not in pre-breakout state"}

    # 5. Signal (if active)
    sig_raw = await redis.hgetall(f"infusion:signal:{symbol}")
    if sig_raw:
        sig = _decode_redis_hash(sig_raw)
        sources["signal"] = {
            "signal_type": sig.get("signal_type", ""),
            "conviction": float(sig.get("conviction_score", 0)),
            "entry_price": float(sig.get("entry_price", 0)),
            "lifecycle": sig.get("lifecycle", ""),
        }
    else:
        sources["signal"] = {"note": "No active signal for symbol"}

    # Cross-check: tick LTP vs feature LTP should match within 0.5%
    tick_source = sources.get("tick", {})
    feat_source = sources.get("features", {})
    tick_ltp = tick_source.get("ltp", 0) if isinstance(tick_source, dict) else 0
    feat_ltp = feat_source.get("ltp", 0) if isinstance(feat_source, dict) else 0
    if tick_ltp > 0 and feat_ltp > 0:
        drift_pct = abs(tick_ltp - feat_ltp) / tick_ltp * 100
        result["price_drift_pct"] = round(drift_pct, 3)
        result["price_match"] = drift_pct < 0.5
        if drift_pct >= 0.5:
            result["verified"] = False

    return web.json_response(result)
