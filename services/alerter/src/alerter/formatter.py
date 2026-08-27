"""Deterministic Telegram message formatter for options-first signals.

Deliberately short. Per direct feedback: score, MTF colour dots, entry/SL/
T1/T2/T3, and a chaseable flag are what actually drive the go/no-go call —
everything else (trade-map prose, option-contract paragraphs, why/blocker
walls of text) was noise competing with the signal. The full story (why the
signal fired, structure/pattern detail, option chain readiness, suppression
history) is still available in the dashboard's Stock Detail tab and via the
/explain AI advisor — this message just answers "should I look."

"Telegram Redesign & Token Modal" sprint (2026-08-27): the body below the
headline is now a single MarkdownV2 code block (an "institutional
monospace" fixed-width table) rather than plain escaped lines. Two
Telegram-specific facts drive the split:
  - MarkdownV2 only lets `*bold*` apply OUTSIDE a code/pre entity --
    Telegram does not parse markdown inside one. So the one line that
    should read as a bold headline (grade + symbol + side) stays outside
    the fence, still escaped with the original per-character _escape_md;
    everything inside the fence is the tabular detail.
  - Inside a pre/code entity, Telegram's own escaping rule is much
    narrower than everywhere else in MarkdownV2: only backslash and
    backtick need escaping (see _escape_pre) -- the seven-ish other
    reserved characters _escape_md still handles for the headline line
    are literal, unescaped text once inside the fence. Using _escape_md
    on the table body would have produced backslash-cluttered noise
    (every ".", "-", "(" in a price or a strategy id escaped) for no
    reason, since none of that is being parsed as markdown there anyway.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

_IST = timezone(timedelta(hours=5, minutes=30))
_MD_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
_PRE_SPECIAL = re.compile(r"([\\`])")

_MTF_ORDER = ["1M", "5M", "15M", "1H", "4H", "1D"]
_MTF_ICON = {"G": "🟢", "R": "🔴", "Y": "🟡"}
_GRADE_ICON = {"A+": "🟢", "A": "🟢", "B+": "🟡", "B": "🟡", "C": "🔴"}
_ROW_LABEL_WIDTH = 11


def _escape_md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", str(text))


def _escape_pre(text: str) -> str:
    """MarkdownV2's own narrower escaping rule for text inside a
    ```pre/code``` entity -- see this module's own top-level docstring
    for why this isn't just _escape_md reused."""
    return _PRE_SPECIAL.sub(r"\\\1", str(text))


def _row(label: str, value: str) -> str:
    """One fixed-width `LABEL      value` line for the monospace table --
    the padding is what makes it read as an aligned table once Telegram
    renders the surrounding fence in a monospace font, not decoration."""
    return f"{label:<{_ROW_LABEL_WIDTH}}{value}"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_price(price: Any) -> str:
    price = _num(price)
    if price <= 0:
        return "-"
    return f"Rs {price:,.2f}"


def _fmt_score(value: Any) -> str:
    return f"{_num(value):.0f}"


def _features(payload: dict[str, Any]) -> dict[str, Any]:
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


def _sub_scores(payload: dict[str, Any]) -> dict[str, Any]:
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


def _mtf_dots(fs: dict[str, Any]) -> str:
    dots = fs.get("mtf_dots") or {}
    if not isinstance(dots, dict):
        dots = {}
    return "".join(_MTF_ICON.get(str(dots.get(tf) or "Y"), "🟡") for tf in _MTF_ORDER)


def _sizing_text(sub_scores: dict[str, Any]) -> str:
    sizing = sub_scores.get("position_sizing")
    if not isinstance(sizing, dict) or not sizing.get("lot_count"):
        return "Sizing: below risk budget for this lot size - check manually"
    return f"Sizing: {sizing.get('lot_count')} lot(s) = {sizing.get('quantity')} qty"


def _option_dte_text(payload: dict[str, Any]) -> str:
    """Real days-to-expiry, read from the option-chain context
    engine.py's own _enrich_option_context already attaches when the
    Upstox chain has resolved for this symbol (market.py's own
    "expiry_days" field, the same one journal.py surfaces as a paper
    trade's own expiry_days). Genuinely "-" -- not a fabricated 0 or a
    guessed contract -- until that chain context exists, matching this
    module's own price formatting's honest-dash convention."""
    option_chain = payload.get("option_chain")
    if not isinstance(option_chain, dict):
        return "-"
    dte = option_chain.get("expiry_days")
    if dte is None:
        return "-"
    try:
        return f"{int(float(dte))}d"
    except (TypeError, ValueError):
        return "-"


def format_signal(payload: dict[str, Any]) -> str:
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
    rr = _num(payload.get("risk_reward_ratio"))
    vol_mult = _num(fs.get("rel_vol_20d"))
    dte_text = _option_dte_text(payload)

    chaseable = bool(fs.get("chaseable"))
    chase_text = "Chaseable now" if chaseable else "Wait for trigger"

    created_us = _num(payload.get("created_at_us"))
    ts_str = (
        datetime.fromtimestamp(created_us / 1_000_000, tz=_IST).strftime("%H:%M:%S IST")
        if created_us > 0
        else "-"
    )

    # Bold headline, OUTSIDE the fence -- also what Telegram shows in a
    # push-notification preview, so the highest-signal fact (grade,
    # symbol, side) is what a trader sees before ever opening the chat.
    headline = f"*{grade_icon} {_escape_md(symbol)} — {_escape_md(str(option_bias))}*"

    table_lines = [
        _row("SYMBOL", str(symbol)),
        _row("SETUP", f"{option_bias} ({strategy_name})"),
        # "Timeframe" read literally: every signal here fires off a
        # completed 1-minute bar (feature-engine's own bar-close-driven
        # pipeline) -- there is no separate per-signal timeframe field to
        # report instead of fabricating one, this scanner doesn't
        # generate signals on a chosen timeframe. The real multi-
        # timeframe confluence read (1M/5M/15M/1H/4H/1D dots) is the
        # honest "timeframe" context available, so it's what's shown.
        _row("TIMEFRAME", f"1-min trigger | MTF {_mtf_dots(fs)}"),
        _row("SCORE", f"{_fmt_score(score)} ({grade})"),
        "-" * 30,
        _row("ENTRY", _format_price(entry)),
        _row("STOP LOSS", _format_price(stop)),
        _row("T1", _format_price(t1)),
        _row("T2", _format_price(t2)),
        _row("T3", _format_price(t3)),
        _row("R:R", f"1:{rr:.2f}" if rr > 0 else "-"),
        _row("VOL x20D", f"{vol_mult:.1f}x" if vol_mult > 0 else "-"),
        _row("DTE", dte_text),
        "-" * 30,
        _row("STATUS", chase_text),
        _sizing_text(sub_scores),
        "",
        f"{ts_str}  PAPER-FIRST",
    ]
    table = _escape_pre("\n".join(table_lines))

    return f"{headline}\n```\n{table}\n```"
