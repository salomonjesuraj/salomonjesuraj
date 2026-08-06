"""Grounded OpenAI advisory client for scanner explanations.

The model is never allowed to create or modify signals. It receives a compact
snapshot produced by Infusion and formats that evidence into a readable review.
"""

from __future__ import annotations

import hashlib
import json

import aiohttp
import structlog

logger = structlog.get_logger()

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

ADVISORY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["TRADE_READY", "WATCH", "AVOID"],
        },
        "summary": {"type": "string"},
        "why_trade": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "blockers": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "trigger": {"type": "string"},
        "invalidation": {"type": "string"},
        "option_view": {"type": "string"},
        "risk_note": {"type": "string"},
    },
    "required": [
        "verdict", "summary", "why_trade", "blockers", "trigger",
        "invalidation", "option_view", "risk_note",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are Infusion's options-trading review assistant.
You explain an existing deterministic scanner snapshot; you do not create signals.

Rules:
- Use only the supplied JSON evidence.
- Never invent prices, option premiums, strikes, Greeks, OI, IV, news, or market facts.
- If option-chain data is pending, say it is pending and do not call the trade fully ready.
- Respect the scanner's BUY CE, BUY PE, HOLD, or AVOID direction.
- Distinguish setup quality from execution readiness.
- Be concise, practical, and understandable to an Indian intraday options trader.
- A high underlying score alone is not proof that an option contract is tradable.
- This is decision support, not guaranteed financial advice.
"""


def snapshot_digest(snapshot: dict, mode: str) -> str:
    raw = json.dumps(
        {"mode": mode, "snapshot": snapshot},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


class OpenAIAdvisor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_sec: int,
        session: aiohttp.ClientSession,
    ):
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-5.4-mini"
        self.timeout_sec = timeout_sec
        self.session = session

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def analyze(self, snapshot: dict, mode: str) -> dict:
        mode_instruction = {
            "risk": (
                "Perform a strict pre-trade risk review. Prefer WATCH or AVOID "
                "when execution evidence is missing."
            ),
            "explain": (
                "Explain why the setup has strength, what is missing, and what "
                "must happen before an options entry."
            ),
        }.get(mode, "Explain the setup and its execution risks.")

        payload = {
            "model": self.model,
            "store": False,
            "reasoning": {"effort": "low"},
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "infusion_trade_advisory",
                    "strict": True,
                    "schema": ADVISORY_SCHEMA,
                },
            },
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Task: {mode_instruction}\n"
                        "Infusion evidence JSON:\n"
                        + json.dumps(snapshot, separators=(",", ":"), default=str)
                    ),
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
            async with self.session.post(
                OPENAI_RESPONSES_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
            ) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    message = str((body.get("error") or {}).get("message") or "request_failed")
                    logger.warning(
                        "openai_advisory_failed",
                        status=response.status,
                        error=message[:180],
                    )
                    raise RuntimeError(f"OpenAI {response.status}: {message}")

            text = _output_text(body)
            result = json.loads(text)
            result["source"] = "openai"
            result["model"] = body.get("model", self.model)
            result["response_id"] = body.get("id", "")
            return result
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            logger.warning("openai_advisory_unavailable", error=str(exc)[:200])
            raise


def _output_text(body: dict) -> str:
    for item in body.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    raise RuntimeError("OpenAI response contained no output_text")
