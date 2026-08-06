"""Deterministic Telegram message formatter for options-first signals."""

from __future__ import annotations

import re
import json
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
_MD_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _escape_md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", str(text))


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_price(price) -> str:
    price = _num(price)
    if price <= 0:
        return "-"
    return f"Rs {price:,.2f}"


def _fmt_score(value) -> str:
    value = _num(value)
    return f"{value:.0f}"


def _pct_change(entry: float, target: float) -> str:
    if not entry:
        return "0.0%"
    pct = ((target - entry) / entry) * 100
    return f"{pct:+.1f}%"


def _features(payload: dict) -> dict:
    fs = payload.get("features_snapshot") or {}
    if isinstance(fs, dict):
        return fs
    if isinstance(fs, str):
        try:
            parsed = json.loads(fs)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _line_list(items, max_items=4) -> list[str]:
    if isinstance(items, str):
        try:
            parsed = json.loads(items)
            if isinstance(parsed, list):
                items = parsed
            else:
                items = [x.strip() for x in items.split("|")]
        except json.JSONDecodeError:
            items = [x.strip() for x in items.split("|")]
    if not isinstance(items, list):
        return []
    return [str(x) for x in items[:max_items] if str(x).strip()]


def _dict_value(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _trade_map_line(label: str, trade_map: dict) -> list[str]:
    if not trade_map:
        return []
    side = str(trade_map.get("side") or "-").upper()
    if side in {"HOLD", "AVOID", "WATCH", "-"}:
        bull = _format_price(trade_map.get("bull_above") or trade_map.get("entry"))
        bear = _format_price(trade_map.get("bear_below") or trade_map.get("stop_loss"))
        return [
            f"{label}: {side}",
            f"Watch bull above {bull} / bear below {bear}",
            f"Sustain: {trade_map.get('sustain_rule') or '-'}",
        ]
    return [
        f"{label}: {side}",
        f"Entry {_format_price(trade_map.get('entry'))} | SL {_format_price(trade_map.get('stop_loss'))}",
        f"T1 {_format_price(trade_map.get('target_1'))} | T2 {_format_price(trade_map.get('target_2'))} | R:R {trade_map.get('rr1') or '-'}",
        f"Rule: {trade_map.get('sustain_rule') or '-'}",
    ]


def _direction_zone_lines(zone: dict) -> list[str]:
    if not zone:
        return []
    bias = str(zone.get("bias") or "WAIT").upper()
    state = str(zone.get("state") or "WAIT_ZONE").upper()
    ce_score = _fmt_score(zone.get("ce_score"))
    pe_score = _fmt_score(zone.get("pe_score"))
    ce_above = _format_price(zone.get("ce_above"))
    pe_below = _format_price(zone.get("pe_below"))
    wait_low = _format_price(zone.get("wait_low"))
    wait_high = _format_price(zone.get("wait_high"))
    reason = str(zone.get("reason") or "").strip()
    switch_note = str(zone.get("switch_note") or "").strip()

    if bias == "BUY CE":
        command = f"ACTION: BUY CE only above {ce_above}"
    elif bias == "BUY PE":
        command = f"ACTION: BUY PE only below {pe_below}"
    else:
        command = "ACTION: WAIT / NO CHASE"

    lines = [
        command,
        f"CE above: {ce_above} | PE below: {pe_below}",
        f"No-chase zone: {wait_low} - {wait_high}",
        f"Stable state: {state} | CE {ce_score} / PE {pe_score}",
    ]
    if reason:
        lines.append(f"Reason: {reason}")
    if switch_note:
        lines.append(f"Switch guard: {switch_note}")
    return lines


def _mtf_line(fs: dict) -> str:
    dots = fs.get("mtf_dots") or {}
    if not isinstance(dots, dict):
        dots = {}
    labels = ["1M", "5M", "15M", "1H", "4H", "1D"]
    icon = {"G": "🟢", "R": "🔴", "Y": "🟡"}
    parts = [f"{tf} {icon.get(str(dots.get(tf) or 'Y'), '🟡')}" for tf in labels]
    text = str(fs.get("mtf_text") or "MTF pending")
    source = str(fs.get("mtf_source") or "").strip()
    source_note = " (historical)" if source == "historical_cache" else ""
    return f"MTF: {' | '.join(parts)}{source_note}\n{text}"


def _structure_line(fs: dict) -> str:
    """Same structure/strength vocabulary the TradingView chart shows —
    keeps the alert and the chart telling the same story."""
    trend_text = str(fs.get("trend_text") or "").strip()
    event = str(fs.get("last_event_label") or "").strip()
    pattern = str(fs.get("candle_pattern") or "").strip()
    strength = fs.get("strength_score")
    parts = []
    if trend_text:
        parts.append(trend_text)
    if event and event != "None":
        parts.append(event)
    if pattern:
        parts.append(pattern)
    line = "Structure: " + (" | ".join(parts) if parts else "warming up")
    if strength is not None:
        line += f" | Strength {_fmt_score(strength)}/100"
    return line


def _sizing_line(sub_scores: dict) -> str:
    sizing = sub_scores.get("position_sizing") if isinstance(sub_scores, dict) else None
    if not isinstance(sizing, dict) or not sizing.get("lot_count"):
        return "Sizing: below minimum risk budget for this lot size — verify manually"
    return (
        f"Sizing: {sizing.get('lot_count')} lot(s) x {sizing.get('lot_size')} "
        f"= {sizing.get('quantity')} qty (risk budget Rs {_num(sizing.get('risk_amount')):,.0f})"
    )

def format_signal(payload: dict) -> str:
    symbol = payload.get("symbol", "???")
    strategy_name = payload.get("strategy_id", "unknown")
    signal_type = str(payload.get("signal_type", "")).lower()
    fs = _features(payload)
    option_bias = (
        payload.get("option_bias")
        or fs.get("option_bias")
        or ("BUY PE" if signal_type == "bearish" else "BUY CE")
    )
    score = _num(payload.get("conviction_score"))
    grade = payload.get("conviction_grade", "?")
    rr = _num(payload.get("risk_reward_ratio"))
    sector_id = payload.get("sector_id", "-")
    sector_strength = _num(payload.get("sector_strength"))
    regime = payload.get("market_regime", "-")
    entry = _num(payload.get("entry_price"))
    stop = _num(payload.get("invalidation_price"))
    t1 = _num(fs.get("t1_price") or payload.get("target_price"))
    t2 = _num(fs.get("t2_price") or payload.get("target_price"))
    primary_map = _dict_value(fs.get("primary_trade_map") or payload.get("primary_trade_map"))
    alternate_map = _dict_value(fs.get("mtf_alternate_trade_map") or payload.get("mtf_alternate_trade_map"))
    direction_zone = _dict_value(payload.get("direction_zone"))
    mtf_conflict_note = str(fs.get("mtf_conflict_note") or payload.get("mtf_conflict_note") or "").strip()
    breakout_explanation = str(fs.get("breakout_explanation") or payload.get("breakout_explanation") or "").strip()
    sustain_rule = str(fs.get("sustain_rule") or payload.get("sustain_rule") or "").strip()
    trade_horizon = str(fs.get("trade_horizon") or payload.get("trade_horizon") or "INTRADAY").strip()
    chase_quality = str(fs.get("chase_quality") or payload.get("chase_quality") or "WATCH").strip()
    bull_conf = _num(fs.get("bull_confidence") or payload.get("bull_confidence"))
    bear_conf = _num(fs.get("bear_confidence") or payload.get("bear_confidence"))
    anti_ok = fs.get("anti_chase_ok", payload.get("anti_chase_ok", True))
    anti_ok = bool(anti_ok) and str(anti_ok) not in {"0", "false", "False"}
    rejection_reasons = _line_list(fs.get("rejection_reasons") or payload.get("rejection_reasons"), 4)
    anti_reasons = _line_list(fs.get("anti_chase_reasons") or payload.get("anti_chase_reasons"), 3)
    explanation = _line_list(payload.get("explanation"), 5)
    option_chain = payload.get("option_chain") or {}
    option_metrics = option_chain.get("metrics") or {} if isinstance(option_chain, dict) else {}
    option_status = option_chain.get("execution_status") or ("TRADE_READY" if option_chain.get("trade_ready") else "WAIT_CONTRACT")
    option_grade = option_chain.get("quality_grade") or option_metrics.get("quality_grade") or "-"
    option_blockers = _line_list(option_chain.get("hard_blockers") or option_chain.get("blockers"), 4)

    created_us = _num(payload.get("created_at_us"))
    ts_str = datetime.fromtimestamp(created_us / 1_000_000, tz=_IST).strftime("%H:%M:%S IST") if created_us > 0 else "-"

    stable_bias = str(direction_zone.get("bias") or option_bias or "").upper()
    direction_icon = "??" if "PE" in stable_bias else "??" if "CE" in stable_bias else "?"
    stop_pct = _pct_change(entry, stop)
    t1_pct = _pct_change(entry, t1)
    t2_pct = _pct_change(entry, t2)

    title = "INFUSION PRICE TRIGGER" if strategy_name == "price_trigger" or signal_type == "price_trigger" else "INFUSION OPTIONS ALERT"
    lines = [
        f"{direction_icon} {title} - {symbol} - {stable_bias or option_bias}",
        *(_direction_zone_lines(direction_zone) or [f"ACTION: {option_bias}"]),
        "",
        f"Grade {grade} | Score {_fmt_score(score)} | R:R {rr:.1f}:1",
        f"Raw engine: {option_bias}",
        f"CE conf {_fmt_score(bull_conf)} | PE conf {_fmt_score(bear_conf)}",
        f"Sector {sector_id} {sector_strength:.0f}/100 | Regime {regime}",
        f"Horizon {trade_horizon} | Chase {chase_quality}",
        "",
        f"Entry: {_format_price(entry)}",
        f"SL:    {_format_price(stop)} ({stop_pct})",
        f"T1:    {_format_price(t1)} ({t1_pct})",
        f"T2:    {_format_price(t2)} ({t2_pct})",
        "",
        _mtf_line(fs),
        _structure_line(fs),
        _sizing_line(payload.get("sub_scores") or {}),
        "",
        "Trade map:",
        *(_trade_map_line("Primary", primary_map) or [
            f"Primary: {option_bias}",
            f"Trigger: {_format_price(entry)} | Invalid: {_format_price(stop)}",
            f"Rule: {sustain_rule or '5M/15M close'}",
        ]),
        "",
        *(_trade_map_line("MTF alternate", alternate_map) if alternate_map else []),
        *(["Conflict note:", mtf_conflict_note, ""] if mtf_conflict_note else []),
        *(["AI map:", breakout_explanation, ""] if breakout_explanation else []),
        f"Anti-chase: {'PASS' if anti_ok else 'REJECT / WAIT'}",
    ]

    if anti_reasons:
        lines.extend([f"- {x}" for x in anti_reasons])
    if rejection_reasons:
        lines.append("")
        lines.append("Why not chase:")
        lines.extend([f"- {x}" for x in rejection_reasons])

    lines.append("")
    lines.append("Option contract:")
    if isinstance(option_chain, dict) and option_chain.get("ready"):
        contract = option_chain.get("contract") or "-"
        strike = option_chain.get("strike") or "-"
        expiry = option_chain.get("expiry") or "-"
        expiry_days = option_chain.get("expiry_days") or option_metrics.get("expiry_days") or "-"
        lines.append(f"Status: {option_status} | Contract grade {option_grade}")
        lines.append(f"{symbol} {strike} {('CE' if 'CE' in str(option_bias).upper() else 'PE')} | {expiry}")
        lines.append(f"Key: {contract}")
        lines.append(
            "Premium {premium} | Spread {spread}% | OI {oi} | IV {iv} | Exp {expiry_days}d".format(
                premium=_format_price(option_metrics.get("ltp")),
                spread=option_metrics.get("spread_pct", "-"),
                oi=f"{_num(option_metrics.get('oi')):,.0f}",
                iv=option_metrics.get("iv", "-"),
                expiry_days=expiry_days,
            )
        )
        if option_blockers:
            lines.append("Contract blockers:")
            lines.extend([f"- {x}" for x in option_blockers])
    else:
        lines.append("Pending Upstox chain confirmation - verify contract before entry.")

    if explanation:
        lines.append("")
        lines.append("Reasons:")
        lines.extend([f"- {x}" for x in explanation])

    lines.extend([
        "",
        f"Strategy: {strategy_name}",
        f"Time: {ts_str}",
        "Execution mode: PAPER-FIRST until forward proof and journal expectancy are approved.",
        "Risk: stock-options move fast; avoid market orders on wide spreads.",
    ])

    return _escape_md("\n".join(lines))
