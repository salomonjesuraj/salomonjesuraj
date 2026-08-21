"""FinBERT sentiment classifier -- EBIE EB-7 (increment 2).

Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q2.2 (self-host, CPU-first, no
GPU until measured latency proves CPU inadequate): loads ProsusAI/finbert
(a widely-used, purpose-built finance-domain 3-class sentiment model --
positive/negative/neutral) via Hugging Face `transformers`, batched CPU
inference, no training, no fine-tuning.

Failure mode, per Q4.2: "If sentiment service fails: sentiment =
UNKNOWN, not neutral, not zero, not a scanner crash." A model that
fails to load, or a batch that throws, must never silently produce a
fabricated neutral/zero-confidence result -- callers get `None` back
(see `classify_sentiment`'s per-item None entries and
`FinbertClassifier.available`) and are responsible for treating that
as UNKNOWN, not neutral.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

MODEL_NAME = "ProsusAI/finbert"
MODEL_VERSION = "finbert-prosusai+taxonomy-v1"
# FinBERT's own label order (config.json's id2label) -- verified against
# the model card rather than assumed, since a wrong index here would
# silently swap bullish/bearish.
_LABELS = ["positive", "negative", "neutral"]
_DIRECTION_MAP = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}
# When the top two class probabilities are this close, the model isn't
# genuinely confident in one direction -- report 'ambiguous' rather than
# picking a winner that barely edged out the runner-up.
AMBIGUOUS_MARGIN = 0.15


class FinbertClassifier:
    """Lazy-loaded, CPU-only. Construction never raises -- a load
    failure (no network, HF hub unreachable, OOM, etc.) leaves
    `available=False` and every classify call returns None entries,
    matching the authorized UNKNOWN failure mode rather than crashing
    the service."""

    def __init__(self) -> None:
        self.available = False
        self._tokenizer = None
        self._model = None
        self._load_error: str | None = None

    def load(self) -> None:
        try:
            import torch  # noqa: F401  -- import check happens together with transformers below
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
            self._model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
            self._model.eval()
            self.available = True
            logger.info("finbert_model_loaded", model=MODEL_NAME)
        except Exception as exc:
            self._load_error = str(exc)
            self.available = False
            logger.error("finbert_model_load_failed", error=self._load_error)

    def classify_batch(self, texts: list[str]) -> list[dict | None]:
        """Returns one {direction, confidence} dict per input text, in
        order, or None for any text that couldn't be classified (model
        unavailable, or an unexpected per-batch failure -- the whole
        batch degrades to None entries rather than raising, so one bad
        article never blocks the rest of a sweep)."""
        if not texts:
            return []
        if not self.available or self._model is None or self._tokenizer is None:
            return [None] * len(texts)
        try:
            import torch

            with torch.no_grad():
                encoded = self._tokenizer(
                    texts,
                    padding=True,
                    truncation=True,
                    max_length=256,
                    return_tensors="pt",
                )
                logits = self._model(**encoded).logits
                probs = torch.softmax(logits, dim=-1)

            results: list[dict | None] = []
            for row in probs:
                values = row.tolist()
                pairs = sorted(zip(_LABELS, values, strict=False), key=lambda p: p[1], reverse=True)
                top_label, top_prob = pairs[0]
                second_prob = pairs[1][1]
                if top_label != "neutral" and (top_prob - second_prob) < AMBIGUOUS_MARGIN:
                    direction = "ambiguous"
                else:
                    direction = _DIRECTION_MAP[top_label]
                results.append({"direction": direction, "confidence": round(float(top_prob), 4)})
            return results
        except Exception as exc:
            logger.error("finbert_classify_batch_failed", error=str(exc), batch_size=len(texts))
            return [None] * len(texts)
