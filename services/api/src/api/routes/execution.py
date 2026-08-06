"""Execution staging for Infusion.

This route builds a broker-style order ticket from a journal/setup, but it does
not place orders.  It is the safety bridge between scanner confidence and any
future Upstox order integration.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiohttp import web

from api.cost_model import OptionTradeCostInput, compute
from api.option_reality import derive_option_sl

routes = web.RouteTableDef()

IST = ZoneInfo("Asia/Kolkata")
STAGED_KEY = "infusion:execution:staged_tickets"
MAX_STAGED_ROWS = 200


def _now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _num(value, default: float = 0.0) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return default


def _text(value, default: str = "-") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if str(x).strip()]


def _build_ticket(payload: dict) -> dict:
    trade = payload.get("trade") if isinstance(payload.get("trade"), dict) else payload
    option = trade.get("option") if isinstance(trade.get("option"), dict) else {}
    news = trade.get("news_edge") if isinstance(trade.get("news_edge"), dict) else {}
    ts_ms = int(time.time() * 1000)

    symbol = _text(trade.get("symbol"), "UNKNOWN").upper()
    decision = _text(trade.get("decision"), "WAIT").upper()
    premium = _num(option.get("premium"))
    entry_ask = _num(option.get("ask") or option.get("entry_fill") or option.get("premium"))
    entry_bid = _num(option.get("bid") or option.get("exit_fill_reference") or option.get("premium"))
    delta = _num(option.get("delta") or option.get("delta_used"))
    spread_pct = _num(option.get("spread_pct"))
    liquidity_ok = option.get("liquidity_whitelist_pass")
    physical_settlement_block = bool(option.get("physical_settlement_block"))
    event_calendar = option.get("event_calendar") if isinstance(option.get("event_calendar"), dict) else {}
    risk_amount = _num(trade.get("risk_amount"))
    lot_size = int(_num(option.get("lot_size"), 0) or 0)
    instrument_key = _text(option.get("instrument_key"), "")
    hard_blockers = _list(option.get("hard_blockers"))
    blockers = _list(option.get("blockers")) + _list(trade.get("rejection_reasons")) + _list(news.get("risks"))

    side = "BUY"
    product = "INTRADAY"
    order_type = "LIMIT"
    entry_underlying = _num(trade.get("entry"))
    sl_underlying = _num(trade.get("stop"))
    target1_underlying = _num(trade.get("target1"))
    sl_model = derive_option_sl(entry_underlying, sl_underlying, entry_ask or premium, delta)
    option_sl_pct = _num(sl_model.get("premium_risk_pct"))
    premium_risk = _num(sl_model.get("premium_risk"))
    qty_by_risk = math.floor(risk_amount / premium_risk) if premium_risk > 0 and risk_amount > 0 else 0
    lot_count = max(0, math.floor(qty_by_risk / lot_size)) if lot_size > 0 else 0
    quantity = lot_count * lot_size
    estimated_max_loss = round(quantity * premium_risk, 2) if quantity else 0.0
    cost_preview = compute(
        OptionTradeCostInput(
            entry_ask=entry_ask or premium,
            exit_bid=entry_ask or premium,
            bid_at_entry=entry_bid,
            quantity=quantity or max(lot_size, 1),
        )
    )
    trigger_price = _num(trade.get("positive_above") if "CE" in decision or decision == "BUY" else trade.get("negative_below"))

    gate_blocks: list[str] = []
    if trade.get("status") == "BLOCKED":
        gate_blocks.append("Journal setup is blocked")
    if "BUY" not in decision and "SELL" not in decision:
        gate_blocks.append("No clear CE/PE direction")
    if not option.get("trade_ready"):
        gate_blocks.append("Option contract is not trade-ready")
    if not instrument_key:
        gate_blocks.append("Missing Upstox instrument key")
    if premium <= 0:
        gate_blocks.append("Missing live option premium")
    if lot_size <= 0:
        gate_blocks.append("Missing option lot size")
    if spread_pct > 6:
        gate_blocks.append(f"Spread too wide: {spread_pct:.2f}%")
    if liquidity_ok is False:
        gate_blocks.append("Option fails liquidity whitelist")
    if physical_settlement_block:
        gate_blocks.append("Physical settlement risk: stock option expiry <= 3 days")
    if event_calendar and not event_calendar.get("entry_allowed", True):
        gate_blocks.append(str(event_calendar.get("block_reason") or "Event calendar blocks new entry"))
    if sl_model.get("hard_blockers"):
        gate_blocks.extend(_list(sl_model.get("hard_blockers"))[:4])
    if risk_amount <= 0:
        gate_blocks.append("Risk amount is not set")
    if quantity <= 0:
        gate_blocks.append("Risk budget is too small for current premium/lot")
    if hard_blockers:
        gate_blocks.extend(hard_blockers[:4])

    status = "READY_TO_STAGE" if not gate_blocks else "BLOCKED"
    return {
        "id": f"stage_{ts_ms}_{symbol}",
        "created_at_ist": _now_ist(),
        "mode": "paper_first_no_live_orders",
        "status": status,
        "symbol": symbol,
        "decision": decision,
        "contract": _text(option.get("suggested_contract")),
        "instrument_key": instrument_key,
        "side": side,
        "product": product,
        "order_type": order_type,
        "limit_price": entry_ask or premium,
        "quantity": quantity,
        "lot_size": lot_size,
        "lot_count": lot_count,
        "option_sl_pct": option_sl_pct,
        "estimated_option_sl": _num(sl_model.get("option_sl_price")),
        "premium_risk": premium_risk,
        "underlying_risk": _num(sl_model.get("underlying_risk")),
        "delta_used": _num(sl_model.get("delta_used")),
        "estimated_max_loss": estimated_max_loss,
        "gross_pnl_flat": cost_preview.get("gross_pnl"),
        "total_costs": cost_preview.get("total_costs"),
        "net_pnl_flat": cost_preview.get("net_pnl"),
        "cost_as_pct_of_premium": cost_preview.get("cost_as_pct_of_premium"),
        "liquidity_whitelist_pass": liquidity_ok,
        "physical_settlement_block": physical_settlement_block,
        "event_calendar": event_calendar,
        "next_event_date": _text(option.get("next_event_date") or event_calendar.get("next_event_date"), ""),
        "fill_policy": cost_preview.get("fill_policy"),
        "risk_amount": risk_amount,
        "trigger_price": trigger_price,
        "underlying_entry": entry_underlying,
        "underlying_sl": sl_underlying,
        "underlying_t1": target1_underlying,
        "underlying_t2": _num(trade.get("target2")),
        "scores": {
            "conviction": _num(trade.get("option_readiness")),
            "strength": _num(trade.get("setup_strength")),
            "mtf": _num(trade.get("mtf_score")),
        },
        "option_quality": _text(option.get("quality_grade")),
        "news_stance": _text(news.get("stance"), "NO_NEWS"),
        "blockers": gate_blocks or blockers[:5],
        "source_trade_id": _text(trade.get("id"), ""),
        "warning": "Paper ticket only. Live Upstox order placement is intentionally disabled.",
    }


async def _load_rows(redis, limit: int = 80) -> list[dict]:
    raw_rows = await redis.lrange(STAGED_KEY, 0, max(0, limit - 1))
    rows: list[dict] = []
    for raw in raw_rows:
        try:
            text = raw.decode() if isinstance(raw, bytes) else raw
            rows.append(json.loads(text))
        except Exception:
            continue
    return rows


@routes.post("/api/execution/stage")
async def stage_execution_ticket(request):
    redis = request.app["redis"]
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    ticket = _build_ticket(payload or {})
    if request.query.get("dry_run") == "1":
        return web.json_response({"ok": True, "dry_run": True, "ticket": ticket})
    await redis.lpush(STAGED_KEY, json.dumps(ticket))
    await redis.ltrim(STAGED_KEY, 0, MAX_STAGED_ROWS - 1)
    return web.json_response({"ok": True, "ticket": ticket})


@routes.get("/api/execution/staged")
async def get_staged_tickets(request):
    redis = request.app["redis"]
    limit = int(request.query.get("limit", "60") or 60)
    limit = max(1, min(limit, MAX_STAGED_ROWS))
    rows = await _load_rows(redis, limit)
    ready = len([r for r in rows if r.get("status") == "READY_TO_STAGE"])
    blocked = len([r for r in rows if r.get("status") == "BLOCKED"])
    return web.json_response({"ok": True, "count": len(rows), "ready": ready, "blocked": blocked, "tickets": rows})
