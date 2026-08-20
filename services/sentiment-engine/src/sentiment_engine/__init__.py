"""Sentiment-engine: EBIE EB-7's self-hosted FinBERT news classifier.

The one service split pre-approved in advance (per
docs/EBIE-IMPLEMENTATION-ANSWERS.md Q4.2) -- Transformers/PyTorch
dependencies and model-init lifecycle are isolated from `api`/`scanner`
so an NLP failure can never destabilize price/scanner APIs.
"""
