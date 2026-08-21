"""Deterministic Telegram message formatter for options-first signals.

Deliberately short. Per direct feedback: score, MTF colour dots, entry/SL/
T1/T2/T3, and a chaseable flag are what actually drive the go/no-go call —
everything else (trade-map prose, option-contract paragraphs, why/blocker
walls of text) was noise competing with the signal. The full story (why the
signal fired, structure/pattern detail, option chain readiness, suppression
history) is still available in the dashboard's Stock Detail tab and via the
/explain AI advisor — this message just answers "should I look."
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))
_MD_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

_MTF_ORDER = ["1M", "5M", "15M", "1H", "4H", "1D"]
_MTF_ICON = {"G": "🟢", "R": "🔴", "Y": "🟡"}
_GRADE_ICON = {"A+": "🟢", "A": "🟢", "B+": "🟡", "B": "🟡", "C": "🔴"}


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
    return f"{_num(value):.0f}"


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


def _sub_scores(payload: dict) -> dict:
    sub = payload.get("sub_scores") or {}
    if isinstance(sub, dict):
        return sub
    if isinstance(sub, str):
        try:
            parsed = json.loads(sub)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _mtf_dots(fs: dict) -> str:
    dots = fs.get("mtf_dots") or {}
    if not isinstance(dots, dict):
        dots = {}
    return "".join(_MTF_ICON.get(str(dots.get(tf) or "Y"), "🟡") for tf in _MTF_ORDER)


def _sizing_text(sub_scores: dict) -> str:
    sizing = sub_scores.get("position_sizing")
    if not isinstance(sizing, dict) or not sizing.get("lot_count"):
        return "Sizing: below risk budget for this lot size - check manually"
    return f"Sizing: {sizing.get('lot_count')} lot(s) = {sizing.get('quantity')} qty"


def format_signal(payload: dict) -> str:
    symbol = payload.get("symbol", "???")
    strategy_name = payload.get("strategy_id", "unknown")
    signal_type = str(payload.get("signal_type", "")).lower()
    fs = _features(payload)
    sub_scores = _sub_scores(payload)

    option_bias = (
        payload.get("option_bias")
        or fs.get("option_bias")
        or ("BUY PE" if signal_type == "bearish" else "BUY CE")
    )
    score = _num(payload.get("conviction_score"))
    grade = str(payload.get("conviction_grade") or "?")
    grade_icon = _GRADE_ICON.get(grade, "⚪")

    entry = _num(payload.get("entry_price"))
    stop = _num(payload.get("invalidation_price"))
    t1 = _num(fs.get("t1_price") or payload.get("target_price"))
    t2 = _num(fs.get("t2_price"))
    t3 = _num(fs.get("t3_price"))

    chaseable = bool(fs.get("chaseable"))
    chase_text = "🚀 Chaseable" if chaseable else "⏳ Wait for trigger"

    created_us = _num(payload.get("created_at_us"))
    ts_str = (
        datetime.fromtimestamp(created_us / 1_000_000, tz=_IST).strftime("%H:%M:%S IST")
        if created_us > 0
        else "-"
    )

    lines = [
        f"{grade_icon} {symbol} — {option_bias}",
        f"Score {_fmt_score(score)} ({grade})   {chase_text}",
        f"MTF {_mtf_dots(fs)}",
        f"Entry {_format_price(entry)} | SL {_format_price(stop)}",
        f"T1 {_format_price(t1)} | T2 {_format_price(t2)} | T3 {_format_price(t3)}",
        _sizing_text(sub_scores),
        "",
        f"{strategy_name} | {ts_str} | PAPER-FIRST",
    ]

    return _escape_md("\n".join(lines))
