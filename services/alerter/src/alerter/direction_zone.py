"""Stable CE/PE direction-zone enrichment for outbound alerts.

This module mirrors the dashboard's practical decision language:

- BUY CE only after bullish level confirmation.
- BUY PE only after bearish level confirmation.
- WAIT inside the middle zone or during CE/PE conflict.

It does not generate orders or suppress alerts.  It only enriches the payload
so Telegram messages and dashboard decisions speak the same language.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


@dataclass(frozen=True)
class DirectionZone:
    bias: str
    state: str
    ce_score: float
    pe_score: float
    ce_above: float
    pe_below: float
    wait_low: float
    wait_high: float
    reason: str
    switch_note: str = ""

    def as_dict(self) -> dict:
        return {
            "bias": self.bias,
            "state": self.state,
            "ce_score": round(self.ce_score, 1),
            "pe_score": round(self.pe_score, 1),
            "ce_above": round(self.ce_above, 4),
            "pe_below": round(self.pe_below, 4),
            "wait_low": round(self.wait_low, 4),
            "wait_high": round(self.wait_high, 4),
            "reason": self.reason,
            "switch_note": self.switch_note,
        }


def derive_direction_zone(payload: dict, previous_lock: dict | None = None) -> DirectionZone:
    fs = _dict(payload.get("features_snapshot"))
    primary_map = _dict(fs.get("primary_trade_map") or payload.get("primary_trade_map"))
    alternate_map = _dict(fs.get("mtf_alternate_trade_map") or payload.get("mtf_alternate_trade_map"))

    signal_type = str(payload.get("signal_type") or "").lower()
    option_bias = str(payload.get("option_bias") or fs.get("option_bias") or "").upper()
    raw_bias = "BUY PE" if signal_type == "bearish" or "PE" in option_bias else "BUY CE" if signal_type == "bullish" or "CE" in option_bias else "WAIT"

    ltp = _num(payload.get("price_at_signal") or fs.get("ltp") or payload.get("entry_price"))
    ce_above = _num(
        fs.get("positive_above")
        or primary_map.get("bull_above")
        or alternate_map.get("bull_above")
        or payload.get("entry_price")
    )
    pe_below = _num(
        fs.get("negative_below")
        or primary_map.get("bear_below")
        or alternate_map.get("bear_below")
        or payload.get("invalidation_price")
    )
    if ce_above <= 0:
        ce_above = _num(payload.get("entry_price") or ltp)
    if pe_below <= 0:
        pe_below = _num(payload.get("invalidation_price") or ltp)

    option = _num(fs.get("option_readiness") or payload.get("conviction_score"))
    setup = _num(fs.get("setup_strength") or payload.get("conviction_score"))
    mtf = _num(fs.get("mtf_score") or fs.get("pine_confidence") or payload.get("conviction_score"))
    rel_vol = min(10.0, max(0.0, _num(fs.get("rel_vol_20d") or fs.get("rel_vol") or 1.0))) * 1.5
    bull_raw = _num(fs.get("bull_confidence") or payload.get("bull_confidence"))
    bear_raw = _num(fs.get("bear_confidence") or payload.get("bear_confidence"))

    ce_score = bull_raw or (option * 0.38 + setup * 0.24 + mtf * 0.24 + rel_vol)
    pe_score = bear_raw or (option * 0.38 + setup * 0.24 + mtf * 0.24 + rel_vol)
    if raw_bias == "BUY CE":
        ce_score += 8
        pe_score -= 3
    elif raw_bias == "BUY PE":
        pe_score += 8
        ce_score -= 3

    mtf_text = str(fs.get("mtf_text") or "").upper()
    if "STRONG CE" in mtf_text or "CE FOCUS" in mtf_text:
        ce_score += 5
    if "STRONG PE" in mtf_text or "PE FOCUS" in mtf_text:
        pe_score += 5

    ce_score = max(0.0, min(100.0, ce_score))
    pe_score = max(0.0, min(100.0, pe_score))
    gap = abs(ce_score - pe_score)
    ce_crossed = bool(ce_above > 0 and ltp >= ce_above)
    pe_crossed = bool(pe_below > 0 and ltp <= pe_below)

    bias = "WAIT"
    state = "WAIT_ZONE"
    reason = "Inside CE/PE decision zone. Wait for 5M/15M close beyond trigger."

    if ce_score >= 65 and ce_score - pe_score >= 10:
        if ce_crossed or raw_bias == "BUY CE":
            bias = "BUY CE"
            state = "CE_CONFIRMED" if ce_crossed else "WAIT_CE_ABOVE"
            reason = (
                "CE bias confirmed above trigger."
                if ce_crossed
                else "CE has better evidence, but price must sustain above CE trigger."
            )
        else:
            state = "WAIT_CE_ABOVE"
            reason = "CE has better evidence. Wait until spot sustains above CE trigger."
    elif pe_score >= 65 and pe_score - ce_score >= 10:
        if pe_crossed or raw_bias == "BUY PE":
            bias = "BUY PE"
            state = "PE_CONFIRMED" if pe_crossed else "WAIT_PE_BELOW"
            reason = (
                "PE bias confirmed below trigger."
                if pe_crossed
                else "PE has better evidence, but price must sustain below PE trigger."
            )
        else:
            state = "WAIT_PE_BELOW"
            reason = "PE has better evidence. Wait until spot sustains below PE trigger."
    elif gap < 10:
        state = "CONFLICT"
        reason = "CE and PE scores are close. Avoid flip-flop; wait for one side to win clearly."

    switch_note = ""
    if previous_lock:
        prev_bias = str(previous_lock.get("bias") or "").upper()
        prev_ts = _num(previous_lock.get("ts"))
        fresh_ms = (time.time() * 1000.0) - prev_ts if prev_ts else 999999.0
        flipping = prev_bias in {"BUY CE", "BUY PE"} and bias in {"BUY CE", "BUY PE"} and prev_bias != bias
        strong_flip = gap >= 18 and ((bias == "BUY CE" and ce_crossed) or (bias == "BUY PE" and pe_crossed))
        if flipping and fresh_ms < 180000 and not strong_flip:
            switch_note = f"Anti-flip lock: previous {prev_bias}; new {bias} needs stronger trigger."
            bias = "WAIT"
            state = "CONFLICT_LOCK"
            reason = "Direction changed too quickly. Wait for clean candle close and score gap."

    wait_low = min(x for x in [pe_below, ce_above] if x > 0) if (pe_below > 0 or ce_above > 0) else 0.0
    wait_high = max(x for x in [pe_below, ce_above] if x > 0) if (pe_below > 0 or ce_above > 0) else 0.0
    return DirectionZone(bias, state, ce_score, pe_score, ce_above, pe_below, wait_low, wait_high, reason, switch_note)

