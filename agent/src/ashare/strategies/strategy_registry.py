"""Strategy registry for the A-share strategy market.

Strategies are registered by decorating a runner function with
``@register_strategy(definition)``. The runner must accept a single
`StrategyRunRequest` and return a `StrategySnapshot`.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from src.ashare.strategies.market_models import (
    StrategyDefinition,
    StrategyRunRequest,
    StrategySnapshot,
)

logger = logging.getLogger(__name__)


class StrategyRunner(Protocol):
    def __call__(self, request: StrategyRunRequest) -> StrategySnapshot: ...


_REGISTRY: dict[str, tuple[StrategyDefinition, StrategyRunner]] = {}


def register_strategy(definition: StrategyDefinition) -> Callable[[StrategyRunner], StrategyRunner]:
    """Decorator that registers a strategy and its runner."""

    def wrapper(runner: StrategyRunner) -> StrategyRunner:
        if definition.id in _REGISTRY:
            raise ValueError(f"Strategy {definition.id} is already registered")
        _REGISTRY[definition.id] = (definition, runner)
        logger.debug("Registered strategy %s", definition.id)
        return runner

    return wrapper


def get_definition(strategy_id: str) -> StrategyDefinition:
    """Return the definition for a registered strategy."""
    if strategy_id not in _REGISTRY:
        raise KeyError(f"Unknown strategy: {strategy_id}")
    return _REGISTRY[strategy_id][0]


def get_runner(strategy_id: str) -> StrategyRunner:
    """Return the runner for a registered strategy."""
    if strategy_id not in _REGISTRY:
        raise KeyError(f"Unknown strategy: {strategy_id}")
    return _REGISTRY[strategy_id][1]


def list_definitions() -> list[StrategyDefinition]:
    """Return all registered strategy definitions, sorted by name."""
    return [d for d, _ in sorted(_REGISTRY.values(), key=lambda x: x[0].name)]


def list_strategy_ids() -> list[str]:
    """Return all registered strategy IDs."""
    return sorted(_REGISTRY.keys())


def list_market_definitions() -> list[StrategyDefinition]:
    """Return market-visible strategy definitions, sorted by name."""
    return [
        d
        for d, _ in sorted(_REGISTRY.values(), key=lambda x: x[0].name)
        if d.market_visible
    ]


def list_market_strategy_ids() -> list[str]:
    """Return market-visible strategy IDs."""
    return sorted(d.id for d, _ in _REGISTRY.values() if d.market_visible)
