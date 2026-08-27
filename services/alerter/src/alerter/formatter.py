"""Deterministic Telegram message formatter for options-first signals.

"Blended HUD" redesign (2026-08-28): a fixed, explicit template --
🚨 headline with symbol/side/entry, a Grade/Score/Vol summary line, an
"Execution Blueprint" (SL/T1/T2, each with its own % move from entry),
and a "Structural Anchor" (real HTF support/resistance + the close-based
invalidation rule). Replaces the previous sprint's monospace-table
design outright, per direct request -- R:R, T3, MTF confluence dots, and
the position-sizing line are deliberately no longer part of this
message; that's a scope narrowing this template asked for explicitly,
not an oversight.

Parse mode is HTML, not MarkdownV2, by deliberate choice (the request
allowed either). MarkdownV2 reserves `. - ( ) + !` among others -- every
one of which shows up constantly in this exact template (prices,
signed percentages, the "(grade)" parenthetical, "A+"'s own "+"), which
would mean escaping nearly every literal character typed into the
template by hand. HTML's escaping surface is `& < >` only, none of
which this template's own literal punctuation uses -- a real reduction
in how easy this is to get wrong, learned from Phase 1 of the previous
sprint's own live bug (an unescaped literal "(" in a MarkdownV2 message
got a genuine 400 from Telegram's API). `<b>` renders the bold spans the
template's own `**...**` notation calls for.
"""

from __future__ import annotations

import json
from typing import Any

_HTML_SPECIAL = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _escape_html(text: Any) -> str:
    out = str(text)
    for raw, esc in _HTML_SPECIAL.items():
        out = out.replace(raw, esc)
    return out


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


def _pct_move(entry: float, level: float) -> str:
    """Signed % distance of `level` from `entry` -- e.g. a stop below
    entry reads negative, a target above reads positive, for either
    direction (CE or PE), no separate sign convention per side. "N/A"
    (the sprint's own requested safe default), not a fabricated 0%,
    when either price is genuinely unavailable."""
    if entry <= 0 or level <= 0:
        return "N/A"
    return f"{((level - entry) / entry * 100):+.2f}%"


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


def _mtf_structure(payload: dict[str, Any]) -> dict[str, Any]:
    """The real cached HTF support/resistance (engine.py's own
    _enrich_mtf_structure attaches this from api/routes/mtf.py's
    infusion:mtf:{symbol} cache -- the same blocker_up_level/
    blocker_down_level fields api/broker_sync.py's own Position
    Intelligence Engine already reads). {} on a cache miss -- that
    cache is only ever warm for a rolling subset of symbols (see
    scanner/engine.py's own _fetch_mtf_context docstring), a routine
    gap, not a bug."""
    mtf = payload.get("mtf_structure")
    return mtf if isinstance(mtf, dict) else {}


def format_signal(payload: dict[str, Any]) -> str:
    symbol = str(payload.get("symbol") or "???")
    signal_type = str(payload.get("signal_type", "")).lower()
    fs = _features(payload)

    option_bias = str(
        payload.get("option_bias")
        or fs.get("option_bias")
        or ("BUY PE" if signal_type == "bearish" else "BUY CE")
    ).upper()
    bearish = "PE" in option_bias
    side_word = "PUT" if bearish else "CALL"

    score = _num(payload.get("conviction_score"))
    grade = str(payload.get("conviction_grade") or "N/A")
    entry = _num(payload.get("entry_price"))
    stop = _num(payload.get("invalidation_price"))
    t1 = _num(fs.get("t1_price") or payload.get("target_price"))
    t2 = _num(fs.get("t2_price"))

    vol_mult_raw = fs.get("rel_vol_20d")
    vol_text = f"{_num(vol_mult_raw):.1f}x" if vol_mult_raw is not None else "N/A"

    # Structural Anchor: a CALL's meaningful structural level is the
    # real HTF SUPPORT below it (closing below invalidates the long); a
    # PUT's is the real HTF RESISTANCE above it (closing above
    # invalidates the short). The template's own literal mockup only
    # shows a bullish CALL example labeled "Support" -- for a real PUT
    # signal, labeling a resistance level "Support" would be a real,
    # substantive mislabeling, not a cosmetic one, so the label and the
    # close-direction both follow the actual signal's own side instead
    # of always matching the mockup's one worked example.
    mtf = _mtf_structure(payload)
    anchor_level = mtf.get("blocker_up_level") if bearish else mtf.get("blocker_down_level")
    anchor_label = "Resistance" if bearish else "Support"
    anchor_text = _format_price(anchor_level) if anchor_level is not None else "N/A"
    close_op = "&gt;" if bearish else "&lt;"

    lines = [
        f"🚨 <b>BUY {side_word}: {_escape_html(symbol)} @ {_format_price(entry)}</b>",
        f"Grade: {_escape_html(grade)} ({_fmt_score(score)}%) | Vol: {vol_text}",
        "",
        "📍 <b>Execution Blueprint</b>",
        f"• SL : {_format_price(stop)} ({_pct_move(entry, stop)})",
        f"• T1 : {_format_price(t1)} ({_pct_move(entry, t1)})",
        f"• T2 : {_format_price(t2)} ({_pct_move(entry, t2)})",
        "",
        "🛡️ <b>Structural Anchor</b>",
        f"• {anchor_label} : {anchor_text}",
        f"• Invalidation: 1m Close {close_op} {anchor_text}",
    ]
    return "\n".join(lines)
