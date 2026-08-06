"""Strategy registry — maps strategy_id to strategy instance."""

from scanner.strategies.base import BaseStrategy

# Populated by register_strategy() during startup
_REGISTRY: dict[str, BaseStrategy] = {}


def register_strategy(strategy: BaseStrategy) -> None:
    """Register a strategy instance by its strategy_id."""
    _REGISTRY[strategy.strategy_id] = strategy


def get_strategies() -> list[BaseStrategy]:
    """Return all registered strategies (deterministic order)."""
    return list(_REGISTRY.values())


def get_strategy(strategy_id: str) -> BaseStrategy | None:
    return _REGISTRY.get(strategy_id)
