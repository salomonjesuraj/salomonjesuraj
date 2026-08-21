"""Infusion decision-intelligence helpers.

This module is deterministic. It does not predict a future price; it converts
current scanner evidence into a clear decision contract for dashboard rows,
trade workbench panels, and future Telegram alerts.
"""

from __future__ import annotations

from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _zone(score: float) -> str:
    if score >= 80:
        return "STRONG"
    if score >= 65:
        return "GOOD"
    if score >= 50:
        return "WATCH"
    if score >= 35:
        return "WEAK"
    return "AVOID"


def _state_label(decision: str, horizon: str, chase: str) -> str:
    decision = (decision or "HOLD").upper()
    horizon = (horizon or "INTRADAY").upper()
    chase = (chase or "WATCH_ONLY").upper()
    if "AVOID" in decision or horizon == "AVOID":
        return "Avoid"
    if "BUY CE" in decision:
        side = "CE"
    elif "BUY PE" in decision:
        side = "PE"
    else:
        side = "Watch"
    if "HIGHLY" in chase:
        return f"{side} high-conviction"
    if "CLEAN" in chase:
        return f"{side} clean"
    if "RETEST" in chase:
        return f"{side} wait retest"
    return f"{side} watch"


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value:
        return [value]
    return []


def _level_text(value: float) -> str:
    return f"{value:.2f}" if value else "-"


def _dominance_label(entry: dict, decision: str) -> tuple[str, str]:
    ce = _num(entry.get("ce_score") or entry.get("bull_confidence"))
    pe = _num(entry.get("pe_score") or entry.get("bear_confidence"))
    vwap = str(entry.get("vwap_state") or "").upper()
    trend = str(entry.get("trend_bias") or "").upper()
    if ce >= pe + 12 and (decision == "BUY CE" or vwap == "ABOVE" or "BUY" in trend):
        return (
            "BUYERS",
            f"Buyers lead CE {ce:.0f} vs PE {pe:.0f}; price/VWAP confirmation still required.",
        )
    if pe >= ce + 12 and (decision == "BUY PE" or vwap == "BELOW" or "SELL" in trend):
        return (
            "SELLERS",
            f"Sellers lead PE {pe:.0f} vs CE {ce:.0f}; breakdown confirmation still required.",
        )
    return "MIXED", f"No clean domination yet: CE {ce:.0f} vs PE {pe:.0f}. Wait for level break."


def _index_drag_label(entry: dict, sector: float) -> tuple[str, str]:
    # Some rows may not yet carry live index-change fields. Keep this honest.
    nifty = entry.get("nifty_change_pct")
    bank = entry.get("banknifty_change_pct") or entry.get("bank_nifty_change_pct")
    stock = _num(entry.get("change_pct"))
    if nifty is None and bank is None:
        if sector >= 62 and stock < 0:
            return (
                "POSSIBLE_SECTOR_SUPPORT",
                "Stock is red while sector strength is supportive; check NIFTY/BANKNIFTY before shorting.",
            )
        if sector <= 38 and stock > 0:
            return (
                "POSSIBLE_SECTOR_DRAG",
                "Stock is green but sector is weak; demand stronger confirmation before chasing CE.",
            )
        return (
            "NEEDS_INDEX_JOIN",
            "Index drag needs live NIFTY/BANKNIFTY fields joined into scanner rows.",
        )
    nifty_chg = _num(nifty)
    bank_chg = _num(bank)
    index_chg = bank_chg if abs(bank_chg) > abs(nifty_chg) else nifty_chg
    if stock >= 0.25 and index_chg <= -0.25:
        return (
            "RELATIVE_STRENGTH",
            "Stock is holding green while index is weak; bounce/follow-through candidate if trigger sustains.",
        )
    if stock <= -0.25 and index_chg >= 0.25:
        return (
            "RELATIVE_WEAKNESS",
            "Stock is weak despite positive index; avoid CE unless it reclaims trigger.",
        )
    if stock < 0 and index_chg < 0 and sector >= 60:
        return (
            "INDEX_PULLBACK",
            "Sector is strong but index pressure is dragging price; watch for reclaim before CE.",
        )
    return "NORMAL", "No special index-drag edge detected."


def _build_command_center(
    *,
    entry: dict,
    features: dict,
    option_summary: dict,
    news_confirmation: dict,
    event_risk: dict,
    decision: str,
    intelligence_horizon: str,
    action_quality: str,
    composite: float,
    blocker: str,
    primary_reason: str,
    sector: float,
    anti_chase_ok: bool,
) -> dict:
    above = _num(entry.get("positive_above"))
    below = _num(entry.get("negative_below"))
    entry_hint = _num(entry.get("entry_price_hint") or entry.get("ltp"))
    stop = _num(entry.get("stop_loss_hint"))
    t1 = _num(entry.get("target_1_hint") or entry.get("move_to"))
    t2 = _num(entry.get("target_2_hint") or entry.get("extended_move_to"))
    t3 = _num(entry.get("target_3_hint")) or (
        t2 + abs(t2 - t1)
        if t1 and t2 and decision == "BUY CE"
        else t2 - abs(t2 - t1)
        if t1 and t2 and decision == "BUY PE"
        else 0.0
    )
    fibo_s2 = _num(entry.get("fibo_s2"))
    fibo_r2 = _num(entry.get("fibo_r2"))
    atr_stop = _num(entry.get("atr_trail_stop"))
    day_low = _num(features.get("day_low") or entry.get("day_low"))
    day_high = _num(features.get("day_high") or entry.get("day_high"))
    support_candidates = [v for v in [below, fibo_s2, atr_stop, day_low] if v > 0]
    resistance_candidates = [v for v in [above, fibo_r2, day_high] if v > 0]
    support = max(
        [v for v in support_candidates if entry_hint <= 0 or v <= entry_hint]
        or support_candidates
        or [0.0]
    )
    resistance = min(
        [v for v in resistance_candidates if entry_hint <= 0 or v >= entry_hint]
        or resistance_candidates
        or [0.0]
    )
    sustain = str(entry.get("sustain_rule") or "5M/15M close")
    dominance, dominance_reason = _dominance_label(entry, decision)
    index_state, index_note = _index_drag_label(entry, sector)
    chain_ready = bool(option_summary.get("option_chain_ready") or option_summary.get("ready"))
    metrics = (
        option_summary.get("metrics") if isinstance(option_summary.get("metrics"), dict) else {}
    )
    spread = _num(metrics.get("spread_pct") or entry.get("chain_spread_pct"))
    oi = _num(metrics.get("oi") or entry.get("chain_oi"))
    iv = _num(metrics.get("iv") or entry.get("chain_iv"))
    premium = _num(metrics.get("ltp") or metrics.get("premium"))
    option_state = str(
        option_summary.get("execution_status")
        or entry.get("chain_execution_status")
        or ("CHAIN_READY" if chain_ready else "CHAIN_PENDING")
    ).upper()
    news_state = str(news_confirmation.get("state") or "NO_NEWS").upper()

    if decision == "BUY CE":
        headline = f"CE only above {_level_text(above)}; no chase inside {_level_text(below)}-{_level_text(above)}."
        action_text = f"If price sustains above {_level_text(above)} on {sustain}, plan CE with SL {_level_text(stop)} and targets {_level_text(t1)}, {_level_text(t2)}, {_level_text(t3)}."
        fail_text = (
            f"If price loses {_level_text(below)}, bullish view fails; avoid CE and reassess PE."
        )
    elif decision == "BUY PE":
        headline = f"PE only below {_level_text(below)}; no chase inside {_level_text(below)}-{_level_text(above)}."
        action_text = f"If price sustains below {_level_text(below)} on {sustain}, plan PE with SL {_level_text(stop)} and targets {_level_text(t1)}, {_level_text(t2)}, {_level_text(t3)}."
        fail_text = (
            f"If price reclaims {_level_text(above)}, bearish view fails; avoid PE and reassess CE."
        )
    else:
        headline = f"Watch zone: CE above {_level_text(above)}, PE below {_level_text(below)}."
        action_text = "No blind trade. Wait for closed-candle acceptance, volume, and option-chain confirmation."
        fail_text = "If price stays inside the wait zone, skip the trade."

    chase_text = (
        "Clean to consider after chart confirmation." if anti_chase_ok else f"No chase: {blocker}"
    )
    if action_quality not in {"TRADE_READY", "WATCHLIST", "WAIT_CONFIRMATION"}:
        chase_text = f"Do not execute yet: {blocker}"

    return {
        "headline": headline,
        "action_text": action_text,
        "fail_text": fail_text,
        "why": primary_reason,
        "blocker": blocker,
        "chase_text": chase_text,
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "ce_above": round(above, 2),
        "pe_below": round(below, 2),
        "entry": round(entry_hint, 2),
        "stop_loss": round(stop, 2),
        "target_1": round(t1, 2),
        "target_2": round(t2, 2),
        "target_3": round(t3, 2),
        "sustain_rule": sustain,
        "dominance": dominance,
        "dominance_reason": dominance_reason,
        "index_drag": index_state,
        "index_drag_note": index_note,
        "news_state": news_state,
        "news_message": news_confirmation.get("message") or "No news edge available.",
        "option_state": option_state,
        "option_contract": option_summary.get("contract")
        or option_summary.get("suggested_contract")
        or entry.get("chain_suggested_contract")
        or "",
        "option_evidence": {
            "premium": round(premium, 2) if premium else None,
            "spread_pct": round(spread, 2) if spread else None,
            "oi": round(oi, 0) if oi else None,
            "iv": round(iv, 2) if iv else None,
            "chain_ready": chain_ready,
        },
        "horizon": intelligence_horizon,
        "quality": action_quality,
        "score": round(composite, 1),
    }


def build_intelligence_layer(
    entry: dict,
    features: dict | None = None,
    option_summary: dict | None = None,
    news_edge: dict | None = None,
    event_risk: dict | None = None,
) -> dict:
    """Build a stable dashboard-facing intelligence snapshot."""
    features = features or {}
    option_summary = option_summary or {}
    news_edge = news_edge or {}
    event_risk = event_risk or {"entry_allowed": True, "event_type": "NONE"}

    intraday = _num(entry.get("intraday_score"))
    swing = _num(entry.get("swing_score"))
    option = _num(entry.get("option_readiness") or entry.get("conviction_score"))
    setup = _num(entry.get("setup_strength") or entry.get("readiness"))
    mtf = _num(entry.get("mtf_score") or entry.get("pine_confidence") or option)
    rel_vol = _num(entry.get("rel_vol") or features.get("rel_vol_20d"))
    sector = _num(entry.get("sector_strength"), 50.0)
    rr = _num(entry.get("risk_reward_ratio_hint"))

    decision = str(entry.get("trade_decision") or "HOLD").upper()
    horizon = str(entry.get("trade_horizon") or "INTRADAY").upper()
    chase = str(entry.get("chase_quality") or "WATCH_ONLY").upper()
    anti_chase_ok = entry.get("anti_chase_ok") is not False
    rejection_reasons = (
        entry.get("rejection_reasons") if isinstance(entry.get("rejection_reasons"), list) else []
    )
    strength_reasons = (
        entry.get("strength_reasons") if isinstance(entry.get("strength_reasons"), list) else []
    )

    news_confirmation = build_news_confirmation(entry, features, news_edge)
    news_score = _num(news_confirmation.get("score"), 50.0)
    research_edge = 50.0
    backtest_edge = 50.0
    chain_ready = bool(option_summary.get("option_chain_ready") or option_summary.get("ready"))
    if chain_ready:
        option = _num(
            option_summary.get("execution_score")
            or option_summary.get("option_score")
            or option_summary.get("final_score"),
            option,
        )
    option_source = "upstox_option_chain" if chain_ready else "proxy_underlying_until_chain"
    option_status = str(option_summary.get("execution_status") or "").upper()
    option_hard_blockers = (
        option_summary.get("hard_blockers")
        if isinstance(option_summary.get("hard_blockers"), list)
        else []
    )
    option_trade_ready = bool(option_summary.get("trade_ready"))
    event_entry_allowed = event_risk.get("entry_allowed", True) is not False

    evidence_quality = (
        min(len(strength_reasons), 5) * 6.0
        + (10.0 if anti_chase_ok else -8.0)
        + min(rel_vol, 3.0) * 4.0
        + (8.0 if rr >= 1.6 else 0.0)
        - min(len(rejection_reasons), 4) * 5.0
    )
    composite = _clip(
        intraday * 0.24
        + swing * 0.20
        + option * 0.24
        + mtf * 0.12
        + setup * 0.08
        + sector * 0.05
        + news_score * 0.03
        + research_edge * 0.02
        + backtest_edge * 0.02
        + evidence_quality * 0.10
    )
    if not event_entry_allowed:
        composite = _clip(composite - 18.0)
    if chain_ready and not option_trade_ready:
        if option_hard_blockers or option_status == "AVOID_CONTRACT":
            composite = min(composite, 54.0)
        elif option_status in {"CHAIN_PENDING", "WAIT_CONTRACT"}:
            composite = min(composite, 68.0)

    if not event_entry_allowed:
        intelligence_horizon = "AVOID"
    elif horizon == "SWING" and intraday >= 62:
        intelligence_horizon = "BOTH"
    elif horizon in {"SWING", "BTST_1_2D", "INTRADAY", "AVOID"}:
        intelligence_horizon = horizon
    elif swing >= 72 and intraday >= 62:
        intelligence_horizon = "BOTH"
    elif swing >= 72:
        intelligence_horizon = "SWING"
    elif intraday >= 62:
        intelligence_horizon = "INTRADAY"
    else:
        intelligence_horizon = "AVOID" if composite < 45 else "WATCH"

    if not event_entry_allowed:
        action_quality = "EVENT_BLOCK"
    elif chain_ready and not option_trade_ready and option_hard_blockers:
        action_quality = "AVOID_CONTRACT"
    elif (
        chain_ready
        and not option_trade_ready
        and option_status in {"CHAIN_PENDING", "WAIT_CONTRACT"}
    ):
        action_quality = "WAIT_CONTRACT"
    elif news_confirmation.get("state") == "CONFLICTING" and decision in {"BUY CE", "BUY PE"}:
        action_quality = "WAIT_CONFIRMATION"
    elif decision in {"BUY CE", "BUY PE"} and composite >= 72 and anti_chase_ok and rr >= 1.5:
        action_quality = "TRADE_READY"
    elif decision in {"BUY CE", "BUY PE"} and composite >= 60:
        action_quality = "WAIT_CONFIRMATION"
    elif composite >= 50:
        action_quality = "WATCHLIST"
    else:
        action_quality = "NO_TRADE"

    direction_word = (
        "bullish" if decision == "BUY CE" else "bearish" if decision == "BUY PE" else "neutral"
    )
    above = _num(entry.get("positive_above"))
    below = _num(entry.get("negative_below"))
    t1 = _num(entry.get("target_1_hint") or entry.get("move_to"))
    t2 = _num(entry.get("target_2_hint") or entry.get("extended_move_to"))
    sustain = str(entry.get("sustain_rule") or "5M/15M close")
    if decision == "BUY PE":
        map_summary = (
            f"Bearish only below {below:.2f}; sustain {sustain}; targets {t1:.2f}/{t2:.2f}."
        )
    elif decision == "BUY CE":
        map_summary = (
            f"Bullish only above {above:.2f}; sustain {sustain}; targets {t1:.2f}/{t2:.2f}."
        )
    else:
        map_summary = (
            f"Watch above {above:.2f} for CE and below {below:.2f} for PE; wait for confirmation."
        )

    if not event_entry_allowed:
        blocker = str(event_risk.get("block_reason") or "Event calendar blocks new entry")
    elif chain_ready and not option_trade_ready and option_hard_blockers:
        blocker = str(option_hard_blockers[0])
    elif chain_ready and not option_trade_ready and option_summary.get("score_cap_detail"):
        blocker = str(option_summary.get("score_cap_detail"))
    elif news_confirmation.get("state") == "CONFLICTING":
        blocker = str(news_confirmation.get("message") or "News conflicts with scanner")
    else:
        blocker = (
            rejection_reasons[0]
            if rejection_reasons
            else ("Anti-chase clean" if anti_chase_ok else "Retest needed")
        )
    primary_reason = (
        strength_reasons[0]
        if strength_reasons
        else str(entry.get("mtf_text") or "Evidence building")
    )
    summary = (
        f"{_state_label(decision, intelligence_horizon, chase)} - "
        f"{direction_word} bias - {primary_reason}. Block: {blocker}."
    )

    command_center = _build_command_center(
        entry=entry,
        features=features,
        option_summary=option_summary,
        news_confirmation=news_confirmation,
        event_risk=event_risk,
        decision=decision,
        intelligence_horizon=intelligence_horizon,
        action_quality=action_quality,
        composite=composite,
        blocker=blocker,
        primary_reason=primary_reason,
        sector=sector,
        anti_chase_ok=anti_chase_ok,
    )

    layer = {
        "version": "phase1_repo_review_contract",
        "composite_score": round(composite, 1),
        "zone": _zone(composite),
        "action_quality": action_quality,
        "horizon": intelligence_horizon,
        "scores": {
            "intraday": round(intraday, 1),
            "swing": round(swing, 1),
            "options": round(option, 1),
            "mtf": round(mtf, 1),
            "setup": round(setup, 1),
            "news": round(news_score, 1),
            "research": round(research_edge, 1),
            "backtest": round(backtest_edge, 1),
        },
        "sources": {
            "technical": "live_upstox_features",
            "levels": entry.get("level_source") or "phase1_live_proxy",
            "mtf": entry.get("mtf_source") or "proxy_until_historical_cache",
            "options": option_source,
            "news": news_confirmation.get("source") or "cached_public_news_plus_price_volume",
            "research": "pending_phase4_swing_trigger_research",
            "backtest": "pending_phase5_strategy_validation",
        },
        "news_confirmation": news_confirmation,
        "event_calendar": event_risk,
        "option_confirmation": {
            "state": option_status
            or (
                "TRADE_READY"
                if option_trade_ready
                else "CHAIN_PENDING"
                if not chain_ready
                else "WAIT_CONTRACT"
            ),
            "trade_ready": option_trade_ready,
            "score": round(option, 1),
            "raw_score": option_summary.get("raw_option_score"),
            "grade": option_summary.get("quality_grade"),
            "score_cap_reason": option_summary.get("score_cap_reason") or "",
            "score_cap_detail": option_summary.get("score_cap_detail") or "",
            "contract": option_summary.get("contract")
            or option_summary.get("suggested_contract")
            or "",
            "hard_blockers": option_hard_blockers,
        },
        "map_summary": map_summary,
        "primary_reason": primary_reason,
        "primary_blocker": blocker,
        "command_center": command_center,
    }

    return {
        "intelligence_score": round(composite, 1),
        "intelligence_zone": layer["zone"],
        "intelligence_action": action_quality,
        "intelligence_horizon": intelligence_horizon,
        "intelligence_summary": summary,
        "trade_map_summary": map_summary,
        "command_center": command_center,
        "news_edge_score": round(news_score, 1),
        "news_confirmation": news_confirmation,
        "event_calendar": event_risk,
        "research_edge_score": round(research_edge, 1),
        "backtest_edge_score": round(backtest_edge, 1),
        "intelligence_layer": layer,
    }


def build_news_confirmation(
    entry: dict, features: dict | None = None, news_edge: dict | None = None
) -> dict:
    """Confirm public-news stance only when price and volume agree.

    Public feeds can be slow or noisy. For options, news becomes useful only
    after the tape confirms it through VWAP/location and volume expansion.
    """
    features = features or {}
    news_edge = news_edge or {}
    stance = str(news_edge.get("stance") or "NO_NEWS").upper()
    confidence = str(news_edge.get("confidence") or "LOW").upper()
    raw_score = _num(news_edge.get("score"))
    decision = str(entry.get("trade_decision") or "HOLD").upper()
    vwap_state = str(entry.get("vwap_state") or "").upper()
    change_pct = _num(entry.get("change_pct") or features.get("change_pct"))
    rel_vol = _num(entry.get("rel_vol") or features.get("rel_vol_20d"))

    if not news_edge or stance == "NO_NEWS":
        return {
            "state": "NO_NEWS",
            "score": 50.0,
            "stance": "NO_NEWS",
            "confidence": "LOW",
            "message": "No cached fresh news; scanner/option gates remain primary.",
            "risks": [],
            "boosters": [],
            "blockers": ["No fresh cached articles"],
            "source": "no_cached_news",
        }

    if stance == "EVENT_RISK":
        return {
            "state": "EVENT_RISK",
            "score": 42.0,
            "stance": stance,
            "confidence": confidence,
            "message": "Public news indicates event risk; wait for price confirmation and reduce size.",
            "risks": news_edge.get("risks") or [],
            "boosters": news_edge.get("boosters") or [],
            "blockers": news_edge.get("blockers") or [],
            "source": "cached_public_news_event_risk",
        }

    bullish_tape = vwap_state == "ABOVE" and change_pct >= 0 and rel_vol >= 1.2
    bearish_tape = vwap_state == "BELOW" and change_pct <= 0 and rel_vol >= 1.2
    wants_ce = decision == "BUY CE"
    wants_pe = decision == "BUY PE"
    news_bull = stance == "BULLISH"
    news_bear = stance == "BEARISH"

    aligns = (news_bull and wants_ce) or (news_bear and wants_pe) or decision == "HOLD"
    tape_confirms = (news_bull and bullish_tape) or (news_bear and bearish_tape)
    conflicts = (news_bull and wants_pe) or (news_bear and wants_ce)

    if stance == "NEUTRAL":
        state = "NEUTRAL"
        score = 50.0
        message = "News is neutral; use technical and option gates."
    elif conflicts:
        state = "CONFLICTING"
        score = 35.0
        message = f"{stance.title()} news conflicts with {decision}; wait."
    elif aligns and tape_confirms:
        state = "CONFIRMED"
        score = 78.0 + min(abs(raw_score), 2.0) * 4.0
        message = f"{stance.title()} news confirmed by VWAP + volume."
    elif aligns:
        state = "UNCONFIRMED"
        score = 58.0
        message = f"{stance.title()} news found, but tape confirmation is incomplete."
    else:
        state = "WATCH"
        score = 52.0
        message = f"{stance.title()} news is context only until scanner bias agrees."

    if confidence == "HIGH":
        score += 3.0
    elif confidence == "LOW" and state == "CONFIRMED":
        score -= 5.0

    return {
        "state": state,
        "score": round(_clip(score), 1),
        "stance": stance,
        "confidence": confidence,
        "message": message,
        "risks": news_edge.get("risks") or [],
        "boosters": news_edge.get("boosters") or [],
        "blockers": news_edge.get("blockers") or [],
        "source": "cached_public_news_plus_price_volume",
    }
