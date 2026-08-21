"""Telegram Bot API client — raw httpx POST, no SDK.

Sends messages via the Telegram Bot API using httpx.AsyncClient.
Bounded retries with exponential backoff (2, 4, 8 seconds).
If bot_token is empty, operates in dry-run mode (logs but doesn't send).

Design decisions:
  - Raw httpx POST to avoid python-telegram-bot dependency
  - Bounded: max 3 retries, 10s connect + 15s read timeout
  - Returns DeliveryOutcome dataclass for structured result handling
  - Dry-run mode when token is empty (for testing/staging)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import structlog

from alerter.config import AlerterSettings

logger = structlog.get_logger()

_BASE_URL = "https://api.telegram.org/bot"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Result of a Telegram send attempt."""

    success: bool
    status_code: int = 0
    retries: int = 0
    error_msg: str = ""


class TelegramClient:
    """Async Telegram Bot API client using raw httpx POST.

    Usage:
        client = TelegramClient(bot_token, settings)
        outcome = await client.send_message(chat_id, text)
        await client.close()
    """

    def __init__(self, bot_token: str, settings: AlerterSettings):
        self._token = bot_token
        self._retry_max = settings.retry_max
        self._retry_base_sec = settings.retry_base_sec
        self._dry_run = not bot_token  # empty token → dry-run
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0),
        )

        if self._dry_run:
            logger.info("telegram_dry_run", reason="empty_bot_token")

    async def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "MarkdownV2",
    ) -> DeliveryOutcome:
        """Send a message via Telegram Bot API.

        Args:
            chat_id: Telegram chat/channel ID.
            text: Message text (pre-formatted for parse_mode).
            parse_mode: Telegram parse mode (default MarkdownV2).

        Returns:
            DeliveryOutcome with success status, retries, and error info.
        """
        if self._dry_run:
            logger.info(
                "telegram_dry_run_send",
                chat_id=chat_id,
                text_length=len(text),
            )
            return DeliveryOutcome(success=True, retries=0)

        url = f"{_BASE_URL}{self._token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        last_error = ""
        for attempt in range(self._retry_max):
            try:
                response = await self._client.post(url, json=payload)

                if response.status_code == 200:
                    return DeliveryOutcome(
                        success=True,
                        status_code=200,
                        retries=attempt,
                    )

                # Non-retryable client errors (4xx except 429)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    error_body = response.text[:200]
                    logger.warning(
                        "telegram_client_error",
                        status=response.status_code,
                        body=error_body,
                        attempt=attempt + 1,
                    )
                    return DeliveryOutcome(
                        success=False,
                        status_code=response.status_code,
                        retries=attempt,
                        error_msg=f"HTTP {response.status_code}: {error_body}",
                    )

                # Retryable: 429 (rate limit) or 5xx
                last_error = f"HTTP {response.status_code}"
                logger.warning(
                    "telegram_retry",
                    status=response.status_code,
                    attempt=attempt + 1,
                    max_retries=self._retry_max,
                )

            except httpx.TimeoutException as e:
                last_error = f"timeout: {e}"
                logger.warning(
                    "telegram_timeout",
                    attempt=attempt + 1,
                    error=str(e),
                )
            except httpx.HTTPError as e:
                last_error = f"http_error: {e}"
                logger.warning(
                    "telegram_http_error",
                    attempt=attempt + 1,
                    error=str(e),
                )

            # Exponential backoff: 2, 4, 8
            if attempt < self._retry_max - 1:
                backoff = self._retry_base_sec * (2**attempt)
                await asyncio.sleep(backoff)

        # All retries exhausted
        logger.error(
            "telegram_send_failed",
            retries=self._retry_max,
            last_error=last_error,
        )
        return DeliveryOutcome(
            success=False,
            retries=self._retry_max,
            error_msg=last_error,
        )

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
        logger.info("telegram_client_closed")
