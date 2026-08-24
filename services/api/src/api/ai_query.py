"""Phase 12: natural-language query layer over live Infusion state.

Deliberately NOT a free-form "ask the model anything" endpoint. The model
(when configured) never sees the live system directly -- it only ever
rephrases a `facts` dict this module assembled from real Redis/Postgres
reads via functions already built and verified in earlier phases (the
Phase 12 job is orchestration, not new data logic). A fully deterministic,
no-LLM text answer is always produced from the same facts dict, so the
endpoint works identically whether or not OPENAI_API_KEY is configured --
same "advisory_only, deterministic system is the source of truth" contract
as api/ai_advisor.py.

Pipeline: classify_intents() -> gather_facts() -> format_facts_as_text().
Each stage is a pure/near-pure function, independently testable without a
live OpenAI key or a running aiohttp request.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import msgpack

from api.routes.backtest import (
    KNOWN_ABLATION_FIELDS,
    KNOWN_ABLATION_FIELDS_SUB_SCORES,
    compute_feature_ablation,
    compute_optimizer_proposal,
    compute_walkforward,
)
from api.signal_snapshot import build_symbol_snapshot, decode_hash

Payload = dict[str, Any]

SECTOR_PREFIX = "infusion:sector:"
REGIME_KEY = "infusion:regime"
SIGNAL_ACTIVE_KEY = "infusion:signals:active"
SIGNAL_PREFIX = "infusion:signal:"

# Natural-language aliases for the informational fields added across
# Phases 1-10 -- lets "ask about chart patterns" match chart_patterns
# without the user having to know the exact snake_case field name.
_ABLATION_ALIASES: dict[str, tuple[str, str]] = {}
for _f in KNOWN_ABLATION_FIELDS:
    _ABLATION_ALIASES[_f] = (_f, "features_snapshot")
    _ABLATION_ALIASES[_f.replace("_", " ")] = (_f, "features_snapshot")
for _f in KNOWN_ABLATION_FIELDS_SUB_SCORES:
    _ABLATION_ALIASES[_f] = (_f, "sub_scores")
    _ABLATION_ALIASES[_f.replace("_", " ")] = (_f, "sub_scores")
_ABLATION_ALIASES.update(
    {
        "fib": ("fib_targets", "features_snapshot"),
        "fibonacci": ("fib_targets", "features_snapshot"),
        "golden cross": ("ma_regime", "features_snapshot"),
        "death cross": ("ma_regime", "features_snapshot"),
        "moving average regime": ("ma_regime", "features_snapshot"),
        "chart pattern": ("chart_patterns", "features_snapshot"),
        "fair value gap": ("fvg_bullish_ce", "features_snapshot"),
        "fvg": ("fvg_bullish_ce", "features_snapshot"),
        "liquidity sweep": ("last_liquidity_sweep", "features_snapshot"),
        "order block": ("order_block_bullish_validated", "features_snapshot"),
        "donchian": ("donchian_fresh_high_breakout", "features_snapshot"),
        "wyckoff": ("wyckoff_structural_failure", "features_snapshot"),
        "volman": ("volman_entry_triggered", "features_snapshot"),
        "entry timing": ("volman_entry_triggered", "features_snapshot"),
        "cross confirmation": ("cross_confirmation", "sub_scores"),
        "cross-index": ("cross_confirmation", "sub_scores"),
    }
)
del _f


async def load_known_symbols(redis: Any) -> set[str]:
    """The live symbol universe (~200 NSE F&O tickers), for detecting a
    ticker mention in a free-text question. Mirrors routes/ticks.py's
    /api/symbols msgpack-decode of infusion:symbols."""
    raw = await redis.hgetall("infusion:symbols")
    symbols: set[str] = set()
    for meta_raw in raw.values():
        try:
            meta = msgpack.unpackb(meta_raw, raw=False) if isinstance(meta_raw, bytes) else meta_raw
            sym = str(meta.get("symbol") or "").upper()
            if sym:
                symbols.add(sym)
        except Exception:
            continue
    return symbols


def find_mentioned_symbols(question: str, known_symbols: set[str]) -> list[str]:
    tokens = re.findall(r"[A-Za-z&]+", question.upper())
    seen = []
    for tok in tokens:
        if tok in known_symbols and tok not in seen:
            seen.append(tok)
    return seen


def find_mentioned_ablation_field(question: str) -> tuple[str, str] | None:
    q = question.lower()
    # Longest alias first so e.g. "cross confirmation" matches before a
    # shorter, more generic substring would.
    for alias in sorted(_ABLATION_ALIASES, key=len, reverse=True):
        if alias in q:
            return _ABLATION_ALIASES[alias]
    return None


_DIRECTION_CE = re.compile(r"\bce\b|\bcall\b|\bbullish\b|\bbuy\s*ce\b", re.IGNORECASE)
_DIRECTION_PE = re.compile(r"\bpe\b|\bput\b|\bbearish\b|\bbuy\s*pe\b", re.IGNORECASE)


def classify_intents(question: str, known_symbols: set[str]) -> list[Payload]:
    """Deterministic keyword/regex router -- no LLM involved in deciding
    *what* data to fetch, only (optionally) in how to phrase it afterward.
    Returns an ordered list of intent dicts; a question can match several.
    """
    q = question.lower()
    intents: list[Payload] = []

    if re.search(r"\bregime\b|\bmarket\s+(trend|mood|direction)\b|\bbullish\s+or\s+bearish\b", q):
        intents.append({"type": "regime"})

    if re.search(r"\bsector\b|\bsectors\b|\bindustry\b|\bindustries\b", q):
        intents.append({"type": "sectors"})

    if re.search(r"\bsignal(s)?\b|\bfiring\b|\bwhat.?s\s+active\b|\bactive\s+trade", q):
        direction = None
        if _DIRECTION_CE.search(q) and not _DIRECTION_PE.search(q):
            direction = "BUY CE"
        elif _DIRECTION_PE.search(q) and not _DIRECTION_CE.search(q):
            direction = "BUY PE"
        intents.append({"type": "signals", "direction": direction})

    if re.search(
        r"\bwalk.?forward\b|\bout.?of.?sample\b|\bprecision\b.*\btarget\b|\bbacktest\b", q
    ):
        intents.append({"type": "walkforward"})

    if re.search(r"\boptimizer\b|\bproposal\b|\bdrift(ed)?\b|\bshould\s+i\s+change\b|\btune\b", q):
        intents.append({"type": "optimizer_proposal"})

    ablation = find_mentioned_ablation_field(question)
    if ablation and re.search(
        r"\bhelp\b|\bworth\b|\bmatter\b|\blift\b|\bevidence\b|\bshould\s+i\s+use\b|\bworking\b", q
    ):
        field, column = ablation
        intents.append({"type": "feature_ablation", "field": field, "column": column})

    symbols = find_mentioned_symbols(question, known_symbols)
    wants_options = bool(
        re.search(r"\bpcr\b|\bput.?call\b|\bmax\s*pain\b|\boi\b|\bopen\s+interest\b", q)
    )
    for sym in symbols:
        if wants_options:
            intents.append({"type": "options_analytics", "symbol": sym})
        else:
            intents.append({"type": "symbol", "symbol": sym})

    return intents


async def gather_facts(
    intent: Payload,
    *,
    redis: Any,
    pool: Any,
    options_chain_fn: Callable[[Any, str], Awaitable[Payload]],
) -> Payload:
    """Executes one classified intent against real Infusion state.
    `options_chain_fn` is injected (api.routes.market.compute_options_chain_analytics)
    rather than imported directly, to avoid this module depending on
    market.py's heavier Upstox-auth import chain when it isn't needed."""
    itype = intent["type"]

    if itype == "regime":
        data = decode_hash(await redis.hgetall(REGIME_KEY))
        return {"type": "regime", "regime": data or {"regime": "neutral", "reason": "no_data"}}

    if itype == "sectors":
        cursor = 0
        sectors = []
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=f"{SECTOR_PREFIX}*", count=100)
            for key in keys:
                d = await redis.hgetall(key)
                if d:
                    sectors.append(decode_hash(d))
            if cursor == 0:
                break
        sectors.sort(key=lambda x: x.get("rank", 999))
        return {"type": "sectors", "sectors": sectors[:8], "total": len(sectors)}

    if itype == "signals":
        members = await redis.zrevrange(SIGNAL_ACTIVE_KEY, 0, -1, withscores=True)
        signals = []
        for member, score in members:
            m = member.decode() if isinstance(member, bytes) else member
            symbol = m.split(":")[0] if ":" in m else m
            data = await redis.hgetall(
                f"{SIGNAL_PREFIX}{m}" if ":" in m else f"{SIGNAL_PREFIX}{symbol}"
            )
            if not data and ":" in m:
                data = await redis.hgetall(f"{SIGNAL_PREFIX}{symbol}")
            if not data:
                continue
            entry = decode_hash(data)
            entry["symbol"] = symbol
            entry["active_score"] = score
            direction = intent.get("direction")
            if direction and entry.get("option_bias") != direction:
                continue
            signals.append(entry)
        return {
            "type": "signals",
            "direction": intent.get("direction"),
            "signals": signals[:15],
            "total": len(signals),
        }

    if itype == "symbol":
        snapshot = await build_symbol_snapshot(redis, intent["symbol"])
        return {"type": "symbol", "symbol": intent["symbol"], "snapshot": snapshot}

    if itype == "options_analytics":
        result = await options_chain_fn(redis, intent["symbol"])
        return {"type": "options_analytics", "symbol": intent["symbol"], "result": result}

    if itype == "walkforward":
        result = await compute_walkforward(pool)
        return {"type": "walkforward", "result": result}

    if itype == "optimizer_proposal":
        result = await compute_optimizer_proposal(pool, redis)
        return {"type": "optimizer_proposal", "result": result}

    if itype == "feature_ablation":
        result = await compute_feature_ablation(
            pool, field=intent["field"], column=intent["column"]
        )
        return {"type": "feature_ablation", "field": intent["field"], "result": result}

    return {"type": "unknown"}


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def format_facts_as_text(question: str, intents: list[Payload], facts: list[Payload]) -> str:
    """No-LLM deterministic answer. This is the actual grounded answer --
    an OpenAI polish pass (when configured) may only rephrase these exact
    facts, never add to them. Always available, always correct given what
    the deterministic system knows."""
    if not intents:
        return (
            "I couldn't match that to anything I can look up. I can answer "
            "questions about: the current market regime, sector rankings, "
            "active signals (optionally CE/PE only), a specific symbol "
            "(mention its ticker, e.g. RELIANCE), a symbol's PCR/OI/Max "
            "Pain (mention 'PCR' or 'max pain' with the ticker), walk-"
            "forward precision, the optimizer's live-vs-recommended "
            "proposal, or whether a specific Phase 1-10 field (e.g. "
            "'chart patterns', 'wyckoff', 'cross confirmation') shows any "
            "precision lift."
        )

    lines = []
    for fact in facts:
        t = fact["type"]

        if t == "regime":
            r = fact["regime"]
            lines.append(
                f"Market regime: {r.get('regime', 'neutral')} (reason: {r.get('reason', r.get('note', '-'))})."
            )

        elif t == "sectors":
            top = fact["sectors"]
            if not top:
                lines.append("No sector data available yet.")
            else:
                ranked = ", ".join(
                    f"{s.get('rank', '?')}. {s.get('sector_id', s.get('label', '?'))} ({_fmt_pct(s.get('strength_score'))})"
                    for s in top
                )
                lines.append(f"Sector rankings (strongest first): {ranked}.")

        elif t == "signals":
            n = fact["total"]
            direction = fact.get("direction")
            label = f" {direction}" if direction else ""
            if n == 0:
                lines.append(f"No active{label} signals right now.")
            else:
                names = ", ".join(
                    f"{s.get('symbol')} ({s.get('conviction_grade', '-')}, {s.get('conviction_score', 0)})"
                    for s in fact["signals"][:8]
                )
                lines.append(f"{n} active{label} signal(s): {names}{' ...' if n > 8 else ''}.")

        elif t == "symbol":
            snap = fact["snapshot"]
            sc = snap["scanner"]
            mkt = snap["market"]
            struct = snap["structure"]
            if not mkt.get("ltp"):
                lines.append(f"{fact['symbol']}: no live data available right now.")
                continue
            grade_note = (
                f", grade {sc.get('conviction_grade')} ({sc.get('conviction_score')})"
                if sc.get("conviction_grade")
                else ""
            )
            piece = (
                f"{fact['symbol']}: LTP {mkt.get('ltp')} ({_fmt_pct(mkt.get('change_pct'))}), "
                f"scanner decision {sc.get('decision', 'HOLD')}{grade_note}. "
                f"Sector {mkt.get('sector', '-')}, regime {mkt.get('regime', 'neutral')}."
            )
            if struct.get("trend_text"):
                piece += f" Structure: {struct['trend_text']}"
                if struct.get("last_event_label"):
                    piece += f" ({struct['last_event_label']})"
                piece += "."
            lines.append(piece)

        elif t == "options_analytics":
            r = fact["result"]
            if not r.get("ready"):
                lines.append(
                    f"{fact['symbol']} options analytics: unavailable ({r.get('reason', 'unknown reason')})."
                )
            else:
                pcr = r.get("pcr") or {}
                sr = r.get("oi_support_resistance") or {}
                mp = r.get("max_pain") or {}
                parts = []
                if pcr:
                    parts.append(f"PCR {pcr.get('pcr')} ({pcr.get('sentiment', '-')})")
                if sr:
                    parts.append(f"resistance {sr.get('resistance')}, support {sr.get('support')}")
                if mp:
                    parts.append(f"max pain {mp.get('max_pain_strike')}")
                lines.append(
                    f"{fact['symbol']} options: "
                    + (", ".join(parts) if parts else "no data yet")
                    + "."
                )

        elif t == "walkforward":
            r = fact["result"]
            if not r.get("available"):
                lines.append(f"Walk-forward: unavailable ({r.get('reason', 'unknown')}).")
            else:
                rec = r.get("recommended") or {}
                lines.append(
                    f"Walk-forward status: {r.get('status')}. "
                    f"Recommended profile: {rec.get('label', '-')}, "
                    f"out-of-sample precision {_fmt_pct((rec.get('test') or {}).get('precision_pct'))} "
                    f"on {(rec.get('test') or {}).get('decided', 0)} decided trades."
                )

        elif t == "optimizer_proposal":
            r = fact["result"]
            if not r.get("available"):
                lines.append(f"Optimizer proposal: unavailable ({r.get('reason', 'unknown')}).")
            else:
                lines.append(
                    f"Optimizer proposal status: {r.get('status')}. {r.get('note', r.get('reason', ''))}"
                )

        elif t == "feature_ablation":
            r = fact["result"]
            if not r.get("available"):
                lines.append(
                    f"'{fact['field']}' evidence: unavailable ({r.get('reason', 'unknown')})."
                )
            else:
                lines.append(
                    f"'{fact['field']}' evidence: present group {_fmt_pct(r['present']['precision_pct'])} precision "
                    f"({r['present']['decided']} decided) vs absent group {_fmt_pct(r['absent']['precision_pct'])} "
                    f"({r['absent']['decided']} decided). {r.get('note', '')}"
                )

    return " ".join(lines)
