"""Daily recap generator — deterministic end-of-day summary.

Generates a structured text recap from analytics data and publishes
it to the STREAM_RECAP for the alerter to deliver via Telegram.

Schedule: 15:35 IST (triggered by archiver background task)

Design:
  - Pure data — no AI interpretation
  - Deterministic: same day's data → same recap
  - Concise: operational statistics only
  - Delivered via existing alerter pipeline (RECAP event type)
"""

from __future__ import annotations

from datetime import date, timedelta, timezone

import structlog
from infusion_common.timing import now_us
from infusion_models.events import EventType
from infusion_streams.codec import encode_event
from infusion_streams.constants import MAXLEN_SIGNALS, STREAM_SCAN_SIGNALS
from redis.asyncio import Redis

from archiver.analytics import SignalAnalytics

logger = structlog.get_logger()

_IST = timezone(timedelta(hours=5, minutes=30))


def _fmt_pct(val: float | None) -> str:
    """Format percentage or N/A."""
    return f"{val:.1f}%" if val is not None else "N/A"


def _reason_label(reason: str) -> str:
    """Human label for suppression reasons."""
    labels = {
        "duplicate_active": "Repeat signal already active",
        "cooldown_active": "Symbol cooling down",
        "sector_weak": "Sector strength too weak",
        "sector_too_strong_for_pe": "Sector too strong for PE",
        "low_conviction": "Conviction below floor",
        "regime_unfavorable": "Market regime unfavorable",
        "regime_risk_off": "Risk-off regime filter",
    }
    return labels.get(reason, reason.replace("_", " ").title())


def _recap_verdict(active: int, suppressed: int, precision: dict) -> tuple[str, str]:
    """Return deterministic day verdict + operator note."""
    hits = int(precision.get("target_hits") or 0)
    stops = int(precision.get("stop_hits") or 0)

    if active == 0 and suppressed == 0:
        return (
            "NO-TRADE DAY",
            "No confirmed signal fired. Scanner stayed defensive; use watchlist only.",
        )
    if active <= 2:
        if stops > hits:
            return (
                "VERY SELECTIVE / DEFENSIVE",
                "Only a few strict signals fired and at least one failed. Review entry timing before loosening filters.",
            )
        return (
            "VERY SELECTIVE",
            "System allowed only a few trades. Quality control is high, but opportunity flow is low.",
        )
    if hits > stops and hits + stops >= 3:
        return (
            "PRODUCTIVE",
            "Signal flow and hit quality were acceptable. Keep thresholds stable unless tomorrow differs.",
        )
    return (
        "MIXED",
        "There were tradeable signals, but quality needs review by setup/session/sector.",
    )


def format_recap(data: dict) -> str:
    """Format recap data into a deterministic text summary.

    Uses Telegram MarkdownV2 escaping for delivery via alerter.
    """
    td = data["trade_date"]
    data["total_signals"]
    active = data["active_signals"]
    suppressed = data["suppressed_signals"]
    p = data["precision"]
    hits = int(p.get("target_hits") or 0)
    stops = int(p.get("stop_hits") or 0)
    decided = hits + stops
    verdict, verdict_note = _recap_verdict(active, suppressed, p)

    lines = [
        "═" * 38,
        f"📊 INFUSION DAILY RECAP — {td}",
        "═" * 38,
        "",
    ]

    # ── Overview
    lines.append(f"VERDICT: {verdict}")
    lines.append(f"NOTE: {verdict_note}")
    lines.append("")
    lines.append("WHAT HAPPENED:")
    lines.append(f"SIGNALS: {active} active | {suppressed} suppressed")
    if p["total"] > 0:
        lines.append(f"PRECISION: {hits}/{decided} target hits ({_fmt_pct(p['precision_pct'])})")
        lines.append(f"AVG SCORE: {p['avg_score'] or 0:.1f} | AVG R:R: {p['avg_rr'] or 0:.1f}:1")
        if p.get("avg_mfe_pct") is not None:
            lines.append(f"MFE: {p['avg_mfe_pct']:.2f}% | MAE: {p['avg_mae_pct']:.2f}%")
        if decided < 5:
            lines.append(
                f"READ CAREFULLY: only {decided} completed trade(s), so today's precision is not statistically reliable."
            )
        if stops > hits:
            lines.append(
                f"ISSUE: {stops} stop hit(s) vs {hits} target hit(s) - entry timing/trigger quality needs review."
            )
    else:
        lines.append("PRECISION: No tracked outcomes yet")

    # ── By Grade
    if data["by_grade"]:
        lines.append("")
        lines.append("BY GRADE:")
        for g in data["by_grade"]:
            hits = g["target_hits"]
            decided = hits + g["stop_hits"]
            p_pct = _fmt_pct(g["precision_pct"])
            lines.append(f"  {g['grade']:3s}: {g['total']} signals, {hits} wins ({p_pct})")

    # ── By Session
    if data["by_session"]:
        lines.append("")
        lines.append("BY SESSION:")
        _session_labels = {
            "opening": "Opening  (09:15-10:00)",
            "mid_morning": "Mid-morn (10:00-12:00)",
            "midday": "Midday   (12:00-14:00)",
            "closing": "Closing  (14:00-15:30)",
        }
        for s in data["by_session"]:
            label = _session_labels.get(s["session"], s["session"])
            lines.append(f"  {label}: {s['total']} sig, {_fmt_pct(s['precision_pct'])}")

    # ── By Sector
    if data["by_sector"]:
        lines.append("")
        lines.append("SECTORS:")
        for sec in data["by_sector"][:5]:  # top 5
            lines.append(
                f"  {sec['sector_id']}: {sec['total']} sig, {_fmt_pct(sec['precision_pct'])}"
            )

    # ── By Regime
    if data["by_regime"]:
        lines.append("")
        lines.append("REGIME:")
        for reg in data["by_regime"]:
            lines.append(f"  {reg['regime']}: {reg['total']} sig, {_fmt_pct(reg['precision_pct'])}")

    # ── Suppression
    sup = data["suppression"]
    if sup["total_suppressed"] > 0:
        lines.append("")
        lines.append("SUPPRESSION:")
        for r in sup["by_reason"]:
            avg_score = r.get("avg_score")
            score_txt = f", avg score {avg_score:.0f}" if avg_score is not None else ""
            lines.append(
                f"  {r['reason']}: {r['count']} blocked - {_reason_label(r['reason'])}{score_txt}"
            )

        lines.append("")
        lines.append("SUPPRESSION MEANING:")
        for r in sup["by_reason"]:
            reason = r["reason"]
            if reason == "duplicate_active":
                lines.append(
                    "  duplicate_active = not a missed trade; same symbol/strategy was already live."
                )
            elif reason == "sector_weak":
                lines.append(
                    "  sector_weak = setup existed, but sector participation was below the safety floor."
                )
            elif reason == "cooldown_active":
                lines.append("  cooldown_active = avoids repeated alerts after a recent signal.")
            elif reason == "low_conviction":
                lines.append("  low_conviction = setup fired but score was too weak for alert.")

    lines.append("")
    lines.append("NEXT SESSION PLAN:")
    if active <= 2:
        lines.append("  1) Keep strict breakout alerts as A+ only.")
        lines.append("  2) Add WATCH/CANDIDATE alerts for 60-80 score setups.")
        lines.append("  3) Backtest looser entry timing before changing live signal thresholds.")
    else:
        lines.append("  1) Review losing setup details before changing thresholds.")
        lines.append("  2) Prefer sectors with rising strength and cleaner option-chain data.")
        lines.append("  3) Avoid chasing late entries after the first impulse candle.")

    lines.append("")
    lines.append("═" * 38)

    return "\n".join(lines)


async def generate_and_publish_recap(
    analytics: SignalAnalytics,
    redis: Redis,
    trade_date: date | None = None,
) -> str:
    """Generate daily recap and publish to signal stream for alerter delivery.

    Returns the formatted recap text.
    """
    td = trade_date or date.today()

    logger.info("recap_generating", trade_date=td.isoformat())

    data = await analytics.daily_recap_data(td)
    text = format_recap(data)

    # Publish as a RECAP event on the signal stream
    # The alerter will detect event_type and format appropriately
    recap_payload = {
        "signal_id": f"recap-{td.isoformat()}",
        "symbol": "RECAP",
        "strategy_id": "daily_recap",
        "signal_type": "recap",
        "conviction_score": 0,
        "conviction_grade": "RECAP",
        "suppressed": False,
        "created_at_us": now_us(),
        "recap_text": text,
        "recap_data": data,
    }

    encoded = encode_event(EventType.SCAN_SIGNAL, recap_payload, now_us())
    await redis.xadd(
        STREAM_SCAN_SIGNALS,
        {"data": encoded},
        maxlen=MAXLEN_SIGNALS,
        approximate=True,
    )

    logger.info(
        "recap_published",
        trade_date=td.isoformat(),
        text_length=len(text),
        active=data["active_signals"],
        suppressed=data["suppressed_signals"],
    )

    return text
