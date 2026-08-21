"""Per-symbol live snapshot builder — shared by the AI advisory endpoint
(routes/ai.py's /api/ai/analyze/{symbol}) and the Phase 12 NL query layer
(ai_query.py).

Extracted out of routes/ai.py (Phase 12) so ai_query.py can reuse the exact
same grounded snapshot without a circular import: routes/ai.py -> ai_query.py
-> this module, and routes/ai.py -> this module directly for its own route.
Neither of the two ever imports the other.

Pure data assembly -- no OpenAI calls, no route/request objects. Takes a
plain redis client so it's callable from anywhere that has one.
"""

from __future__ import annotations

import json


def decode_hash(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        key = key.decode() if isinstance(key, bytes) else key
        value = value.decode() if isinstance(value, bytes) else value
        if value in {"True", "False"}:
            out[key] = value == "True"
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            try:
                out[key] = json.loads(value) if value and value[0] in "[{" else value
            except (json.JSONDecodeError, IndexError):
                out[key] = value
    return out


async def first_signal(redis, symbol: str) -> dict:
    direct = await redis.hgetall(f"infusion:signal:{symbol}")
    if direct:
        return decode_hash(direct)
    members = await redis.zrevrange("infusion:signals:active", 0, -1)
    for raw in members:
        member = raw.decode() if isinstance(raw, bytes) else raw
        if member.split(":", 1)[0] == symbol:
            data = await redis.hgetall(f"infusion:signal:{member}")
            if data:
                return decode_hash(data)
    return {}


async def _snapshot_reads(redis, symbol: str):
    pipe = redis.pipeline()
    pipe.hgetall(f"infusion:tick:{symbol}")
    pipe.hgetall(f"infusion:feature:{symbol}")
    pipe.hgetall(f"infusion:prebreak:{symbol}")
    pipe.hgetall("infusion:regime")
    tick, feature, prebreak, regime = await pipe.execute()
    signal = await first_signal(redis, symbol)
    return tick, feature, prebreak, regime, signal


async def build_symbol_snapshot(redis, symbol: str) -> dict:
    tick_raw, feature_raw, prebreak_raw, regime_raw, signal = await _snapshot_reads(redis, symbol)
    tick = decode_hash(tick_raw)
    features = decode_hash(feature_raw)
    prebreak = decode_hash(prebreak_raw)
    regime = decode_hash(regime_raw)

    sector_id = str(tick.get("sector_id") or "")
    sector = {}
    if sector_id:
        sector = decode_hash(await redis.hgetall(f"infusion:sector:{sector_id}"))

    bias = signal.get("option_bias") or (
        "BUY CE" if features.get("change_pct", 0) > 0 else "BUY PE"
    )
    option_chain_ready = bool(
        await redis.exists(f"infusion:option-chain:{symbol}")
        or await redis.exists(f"infusion:options:{symbol}")
    )

    # Structure/strength story from feature_engine + scanner (Pine v6
    # alignment). Prefer the signal's frozen features_snapshot when a signal
    # exists; fall back to the live raw feature vector's ml_features dict
    # otherwise, so this section still has something for "explain this
    # stock" queries with no fired signal yet.
    signal_fs = signal.get("features_snapshot")
    signal_fs = signal_fs if isinstance(signal_fs, dict) else {}
    ml = features.get("ml_features")
    ml = ml if isinstance(ml, dict) else {}
    sub_scores = signal.get("sub_scores")
    sub_scores = sub_scores if isinstance(sub_scores, dict) else {}
    sizing = sub_scores.get("position_sizing")
    sizing = sizing if isinstance(sizing, dict) else {}

    return {
        "symbol": symbol,
        "scanner": {
            "decision": bias,
            "signal_type": signal.get("signal_type", ""),
            "conviction_score": signal.get("conviction_score", 0),
            "conviction_grade": signal.get("conviction_grade", ""),
            "entry_price": signal.get("entry_price", 0),
            "invalidation_price": signal.get("invalidation_price", 0),
            "target_price": signal.get("target_price", 0),
            "conditions_met": signal.get("conditions_met", {}),
            "explanation": signal.get("explanation", ""),
        },
        "market": {
            "ltp": tick.get("ltp", features.get("ltp", 0)),
            "change_pct": tick.get("change_pct", features.get("change_pct", 0)),
            "sector": sector_id,
            "regime": regime.get("regime", "neutral"),
        },
        "technical": {
            key: features.get(key)
            for key in (
                "vwap",
                "ema_5",
                "ema_9",
                "ema_20",
                "ema_50",
                "rsi_14",
                "macd",
                "macd_signal",
                "macd_hist",
                "rel_vol_20d",
                "bb_width",
                "squeeze_state",
                "nr_pattern",
                "candle_pattern",
                "atr_14",
                "atr_trail_stop",
                "atr_trend",
                "spread_bps",
                "order_imbalance",
            )
        },
        "setup": {
            "state": prebreak.get("state", ""),
            "readiness_score": prebreak.get("readiness_score", 0),
            "sector_strength": sector.get("strength_score", 0),
            "sector_trend": sector.get("trend", ""),
        },
        "structure": {
            "trend_text": signal_fs.get("trend_text") or ml.get("trend_text", ""),
            "last_event_label": signal_fs.get("last_event_label") or ml.get("last_event_label", ""),
            "supply_zone_top": signal_fs.get("supply_zone_top", ml.get("supply_zone_top")),
            "demand_zone_bottom": signal_fs.get("demand_zone_bottom", ml.get("demand_zone_bottom")),
            "strength_score": signal_fs.get("strength_score"),
            "mtf_text": signal_fs.get("mtf_text", ""),
            "mtf_source": signal_fs.get("mtf_source", ""),
            "recommended_lots": sizing.get("lot_count"),
            "recommended_quantity": sizing.get("quantity"),
        },
        "option_execution": {
            "chain_ready": option_chain_ready,
            "oi_available": False,
            "iv_available": False,
            "contract_spread_available": False,
            "selected_strike_available": False,
        },
    }
