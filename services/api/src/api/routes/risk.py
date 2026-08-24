"""Risk settings for the options-first dashboard.

These settings are intentionally manual and advisory in Phase 1.  They do not
place orders; they tell the scanner how much risk is allowed today and give the
UI a single source of truth for position sizing discipline.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web
from infusion_common.sizing import compute_position_size  # noqa: F401 (re-exported)

routes = web.RouteTableDef()

RISK_KEY = "infusion:risk:settings"
IST = ZoneInfo("Asia/Kolkata")
Payload = dict[str, Any]

DEFAULT_RISK: Payload = {
    "capital": 50000.0,
    "max_daily_loss": 2500.0,
    "risk_per_trade_pct": 1.0,
    "high_conviction_risk_pct": 1.5,
    "max_risk_pct_hard_cap": 5.0,
    "green_day_max_trades": 5,
    "loss_day_max_trades": 2,
    "loss_streak_lock": 2,
    "max_lots": 5,
    "preferred_expiry": "monthly_stock_options",
    "option_style": "BUY_ONLY_CE_PE",
    "auto_orders_enabled": False,
    "execution_mode": "paper_first",
    "trading_signal_mode": "precision",
}

VALID_TRADING_SIGNAL_MODES = {"precision", "opportunity", "aggressive_paper"}


def _num(value: Any, default: float, low: float | None = None, high: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = default
    if low is not None:
        out = max(low, out)
    if high is not None:
        out = min(high, out)
    return out


def _int(value: Any, default: int, low: int | None = None, high: int | None = None) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    if low is not None:
        out = max(low, out)
    if high is not None:
        out = min(high, out)
    return out


def _normalise(raw: Payload | None) -> Payload:
    data = {**DEFAULT_RISK, **(raw or {})}
    capital = _num(data.get("capital"), DEFAULT_RISK["capital"], 1000, 100000000)
    max_daily_loss = _num(
        data.get("max_daily_loss"), DEFAULT_RISK["max_daily_loss"], 0, capital * 0.05
    )
    risk_per_trade_pct = _num(
        data.get("risk_per_trade_pct"), DEFAULT_RISK["risk_per_trade_pct"], 0.1, 5.0
    )
    high_conviction_risk_pct = _num(
        data.get("high_conviction_risk_pct"),
        DEFAULT_RISK["high_conviction_risk_pct"],
        risk_per_trade_pct,
        5.0,
    )
    out = {
        **data,
        "capital": round(capital, 2),
        "max_daily_loss": round(max_daily_loss, 2),
        "max_daily_loss_pct": round((max_daily_loss / capital) * 100, 2) if capital else 0,
        "risk_per_trade_pct": round(risk_per_trade_pct, 2),
        "risk_per_trade_amount": round(capital * risk_per_trade_pct / 100, 2),
        "high_conviction_risk_pct": round(high_conviction_risk_pct, 2),
        "high_conviction_risk_amount": round(capital * high_conviction_risk_pct / 100, 2),
        "max_risk_pct_hard_cap": 5.0,
        "green_day_max_trades": _int(data.get("green_day_max_trades"), 5, 1, 20),
        "loss_day_max_trades": _int(data.get("loss_day_max_trades"), 2, 1, 5),
        "loss_streak_lock": _int(data.get("loss_streak_lock"), 2, 1, 5),
        "max_lots": _int(data.get("max_lots"), DEFAULT_RISK["max_lots"], 1, 50),
        "auto_orders_enabled": False,
        "execution_mode": "paper_first",
        "trading_signal_mode": (
            str(data.get("trading_signal_mode") or DEFAULT_RISK["trading_signal_mode"])
            if str(data.get("trading_signal_mode") or DEFAULT_RISK["trading_signal_mode"])
            in VALID_TRADING_SIGNAL_MODES
            else DEFAULT_RISK["trading_signal_mode"]
        ),
        "updated_at_ist": data.get("updated_at_ist")
        or datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
    }
    return out


async def _load(redis: Any) -> Payload:
    raw = await redis.get(RISK_KEY)
    if not raw:
        return _normalise(None)
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        return _normalise(json.loads(text))
    except Exception:
        return _normalise(None)


@routes.get("/api/risk/settings")
async def get_risk_settings(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    settings = await _load(redis)
    return web.json_response({"ok": True, "settings": settings})


@routes.post("/api/risk/settings")
async def save_risk_settings(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    current = await _load(redis)
    settings = _normalise(
        {
            **current,
            **(payload or {}),
            "updated_at_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        }
    )
    await redis.set(RISK_KEY, json.dumps(settings), ex=60 * 60 * 24 * 30)
    return web.json_response({"ok": True, "settings": settings})
