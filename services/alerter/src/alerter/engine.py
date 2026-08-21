"""Alerter engine orchestrator.

Receives signal payloads from the stream, evaluates the delivery gate,
formats messages, delivers via Telegram, and logs delivery outcomes.

Pipeline per signal:
  1. Extract signal fields from payload
  2. Evaluate delivery gate
  3. If passed: format → deliver → record delivery → log
  4. If blocked: log reason
  5. Update stats

Design principles:
  - Event-driven: one signal → one delivery decision
  - Deterministic: same signal + same Redis state → same outcome
  - Bounded: delivery log capped via LTRIM
  - Auditable: every delivery/block logged with reason
"""

from __future__ import annotations

import json
import time

import structlog
from infusion_streams.constants import KEY_ALERT_LOG
from redis.asyncio import Redis

from alerter.config import AlerterSettings
from alerter.direction_zone import derive_direction_zone
from alerter.gate import DeliveryGate, DeliveryResult
from alerter.telegram import DeliveryOutcome, TelegramClient

logger = structlog.get_logger()


class AlerterEngine:
    """Orchestrates delivery gate evaluation, formatting, and sending."""

    def __init__(
        self,
        redis: Redis,
        settings: AlerterSettings,
        gate: DeliveryGate,
        formatter_fn,
        telegram_client: TelegramClient,
    ):
        self.redis = redis
        self.settings = settings
        self.gate = gate
        self._format_signal = formatter_fn
        self.telegram = telegram_client
        self._chat_id = settings.telegram_chat_id

        # Stats
        self._alerts_sent = 0
        self._alerts_blocked = 0
        self._retries_total = 0
        self._failures_total = 0
        self._by_reason: dict[str, int] = {}
        self._processed = 0

    async def process_signal(self, payload: dict) -> None:
        """Process a single signal payload from the stream.

        This is the core hot path — called for every consumed signal.
        """
        signal_id = payload.get("signal_id", "")
        symbol = payload.get("symbol", "")
        grade = payload.get("conviction_grade", "")
        strategy_id = payload.get("strategy_id", "")

        if not signal_id or not symbol:
            logger.warning("signal_missing_fields", payload_keys=list(payload.keys()))
            return

        self._processed += 1

        # ── RECAP event — bypass gate, send directly ──
        if payload.get("signal_type") == "recap":
            await self._deliver_recap(payload)
            return

        if payload.get("signal_type") == "test_alert":
            await self._deliver_test_alert(payload)
            return

        enriched_payload = await self._enrich_direction_zone(payload, persist=False)

        # ── Evaluate delivery gate ─────────────────────
        result: DeliveryResult = await self.gate.evaluate(
            signal_id=signal_id,
            symbol=symbol,
            grade=grade,
            strategy_id=strategy_id,
        )

        if not result.passed:
            self._alerts_blocked += 1
            self._by_reason[result.reason] = self._by_reason.get(result.reason, 0) + 1
            logger.debug(
                "alert_blocked",
                signal_id=signal_id,
                symbol=symbol,
                grade=grade,
                reason=result.reason,
                tier=result.priority_tier,
            )
            await self._log_delivery(
                signal_id=signal_id,
                symbol=symbol,
                grade=grade,
                outcome="blocked",
                reason=result.reason,
                extra=self._delivery_extra(enriched_payload, result.priority_tier),
            )
            return

        # ── Format message ─────────────────────────────
        enriched_payload = await self._enrich_direction_zone(enriched_payload, persist=True)
        enriched_payload = await self._enrich_option_context(enriched_payload)
        message = self._format_signal(enriched_payload)

        # ── Deliver via Telegram ───────────────────────
        outcome: DeliveryOutcome = await self.telegram.send_message(
            chat_id=self._chat_id,
            text=message,
        )

        self._retries_total += outcome.retries

        if outcome.success:
            self._alerts_sent += 1

            # Record delivery in Redis (cooldown, counters, delivered set)
            await self.gate.record_delivery(
                signal_id=signal_id,
                symbol=symbol,
                cooldown_sec=self.settings.alert_cooldown_sec,
            )

            logger.info(
                "alert_delivered",
                signal_id=signal_id,
                symbol=symbol,
                grade=grade,
                tier=result.priority_tier,
                retries=outcome.retries,
            )
            await self._log_delivery(
                signal_id=signal_id,
                symbol=symbol,
                grade=grade,
                outcome="delivered",
                reason="",
                extra=self._delivery_extra(enriched_payload, result.priority_tier),
            )
        else:
            self._failures_total += 1
            self._by_reason["send_failed"] = self._by_reason.get("send_failed", 0) + 1
            logger.error(
                "alert_delivery_failed",
                signal_id=signal_id,
                symbol=symbol,
                grade=grade,
                error=outcome.error_msg,
                retries=outcome.retries,
            )
            await self._log_delivery(
                signal_id=signal_id,
                symbol=symbol,
                grade=grade,
                outcome="failed",
                reason=outcome.error_msg,
                extra=self._delivery_extra(enriched_payload, result.priority_tier),
            )

    async def _enrich_option_context(self, payload: dict) -> dict:
        """Attach cached Upstox option-chain context to Telegram payload.

        The alerter should not invent contract data. It only includes a real
        contract when the API/option cockpit has already cached Upstox chain
        confirmation in Redis.
        """
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            return payload

        option_bias = str(payload.get("option_bias") or "").upper()
        if not option_bias:
            fs = payload.get("features_snapshot") or {}
            option_bias = str(fs.get("option_bias") or "").upper() if isinstance(fs, dict) else ""
        bias = "CE" if "CE" in option_bias else "PE" if "PE" in option_bias else ""

        keys = []
        if bias:
            keys.append(f"infusion:option-chain:{symbol}:{bias}")
        keys.append(f"infusion:option-chain:{symbol}")

        for key in keys:
            raw = await self.redis.get(key)
            if not raw:
                continue
            try:
                text = raw.decode() if isinstance(raw, bytes) else raw
                option_ctx = json.loads(text)
            except Exception:
                continue
            enriched = dict(payload)
            enriched["option_chain"] = option_ctx
            return enriched
        return payload

    async def _deliver_recap(self, payload: dict) -> None:
        """Deliver daily recap event — bypasses gate, sends text directly."""
        recap_text = payload.get("recap_text", "")
        if not recap_text:
            logger.warning("recap_empty_text")
            return

        # Send as plain text (no MarkdownV2 — recap uses box-drawing chars)
        outcome: DeliveryOutcome = await self.telegram.send_message(
            chat_id=self._chat_id,
            text=recap_text,
            parse_mode="",  # plain text
        )

        if outcome.success:
            self._alerts_sent += 1
            logger.info(
                "recap_delivered",
                trade_date=payload.get("recap_data", {}).get("trade_date", ""),
                text_length=len(recap_text),
            )
        else:
            self._failures_total += 1
            logger.error(
                "recap_delivery_failed",
                error=outcome.error_msg,
            )

    async def _deliver_test_alert(self, payload: dict) -> None:
        """Deliver a marked Telegram test alert without affecting cooldowns."""
        signal_id = payload.get("signal_id", "")
        symbol = payload.get("symbol", "INFUSION_TEST")
        grade = payload.get("conviction_grade", "A+")
        enriched_payload = await self._enrich_direction_zone(payload)
        message = self._format_signal(enriched_payload)
        outcome: DeliveryOutcome = await self.telegram.send_message(
            chat_id=self._chat_id,
            text=message,
        )
        self._retries_total += outcome.retries
        if outcome.success:
            self._alerts_sent += 1
            logger.info("test_alert_delivered", signal_id=signal_id, symbol=symbol)
            await self._log_delivery(
                signal_id=signal_id,
                symbol=symbol,
                grade=grade,
                outcome="delivered",
                reason="test_alert",
                extra=self._delivery_extra(enriched_payload, "test"),
            )
        else:
            self._failures_total += 1
            logger.error(
                "test_alert_failed", signal_id=signal_id, symbol=symbol, error=outcome.error_msg
            )
            await self._log_delivery(
                signal_id=signal_id,
                symbol=symbol,
                grade=grade,
                outcome="failed",
                reason=outcome.error_msg,
                extra=self._delivery_extra(enriched_payload, "test"),
            )

    async def _enrich_direction_zone(self, payload: dict, *, persist: bool = True) -> dict:
        """Attach stable CE/PE zone fields to the outbound alert payload."""
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            return payload

        previous = {}
        lock_key = f"infusion:direction-lock:{symbol}"
        raw = await self.redis.get(lock_key)
        if raw:
            try:
                text = raw.decode() if isinstance(raw, bytes) else raw
                previous = json.loads(text)
            except Exception:
                previous = {}

        zone = derive_direction_zone(payload, previous)
        enriched = dict(payload)
        enriched["direction_zone"] = zone.as_dict()

        if persist and zone.bias in {"BUY CE", "BUY PE"}:
            await self.redis.setex(
                lock_key,
                600,
                json.dumps({"bias": zone.bias, "ts": time.time() * 1000.0}, separators=(",", ":")),
            )

        return enriched

    def _delivery_extra(self, payload: dict, priority_tier: str = "") -> dict:
        """Compact metadata for the dashboard Alert Log timeline."""
        fs = payload.get("features_snapshot") or {}
        if isinstance(fs, str):
            try:
                fs = json.loads(fs)
            except Exception:
                fs = {}
        if not isinstance(fs, dict):
            fs = {}

        dz = payload.get("direction_zone") or {}
        if not isinstance(dz, dict):
            dz = {}
        option_bias = payload.get("option_bias") or fs.get("option_bias") or ""

        return {
            "strategy_id": payload.get("strategy_id", ""),
            "signal_type": payload.get("signal_type", ""),
            "option_bias": option_bias,
            "stable_bias": dz.get("bias") or option_bias,
            "direction_state": dz.get("state", ""),
            "direction_reason": dz.get("reason", ""),
            "ce_score": dz.get("ce_score", fs.get("bull_confidence", 0)),
            "pe_score": dz.get("pe_score", fs.get("bear_confidence", 0)),
            "ce_above": dz.get("ce_above", fs.get("positive_above", payload.get("entry_price", 0))),
            "pe_below": dz.get(
                "pe_below", fs.get("negative_below", payload.get("invalidation_price", 0))
            ),
            "score": payload.get("conviction_score", 0),
            "entry": payload.get("entry_price", 0),
            "stop": payload.get("invalidation_price", 0),
            "target": payload.get("target_price", 0),
            "rr": payload.get("risk_reward_ratio", 0),
            "priority_tier": priority_tier,
            "mtf_text": fs.get("mtf_text", ""),
            "anti_chase_ok": fs.get("anti_chase_ok", True),
        }

    async def _log_delivery(
        self,
        signal_id: str,
        symbol: str,
        grade: str,
        outcome: str,
        reason: str,
        extra: dict | None = None,
    ) -> None:
        """Append delivery record to Redis log list (capped via LTRIM).

        Stores as JSON string in KEY_ALERT_LOG list.
        """
        entry_payload = {
            "signal_id": signal_id,
            "symbol": symbol,
            "grade": grade,
            "outcome": outcome,
            "reason": reason,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if extra:
            entry_payload.update(extra)
        entry = json.dumps(entry_payload, separators=(",", ":"))

        pipe = self.redis.pipeline(transaction=False)
        pipe.lpush(KEY_ALERT_LOG, entry)
        pipe.ltrim(KEY_ALERT_LOG, 0, self.settings.delivery_log_max - 1)
        await pipe.execute()

    @property
    def stats(self) -> dict:
        """Current engine statistics."""
        return {
            "processed": self._processed,
            "alerts_sent": self._alerts_sent,
            "alerts_blocked": self._alerts_blocked,
            "retries_total": self._retries_total,
            "failures_total": self._failures_total,
            "by_reason": dict(self._by_reason),
        }
