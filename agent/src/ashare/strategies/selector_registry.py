"""Selector registry for strategy compare.

Usage:
    from src.ashare.strategies import selector_registry  # side effects
    from src.ashare.strategies.selector_registry import resolve_selector

    fn = resolve_selector("local_select")
    picks = fn(trade_date=date(2025, 1, 1), top_n=20, params={})
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Protocol


class SelectorFn(Protocol):
    def __call__(self, *, trade_date: date, top_n: int, params: dict[str, Any]) -> list[Any]: ...


_REGISTRY: dict[str, SelectorFn] = {}


class UnknownSelectorError(KeyError):
    """Raised when a selector name is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown selector: {name}")
        self.name = name


def register_selector(name: str) -> Callable[[SelectorFn], SelectorFn]:
    """Decorator that registers a selector under ``name``."""

    def decorator(fn: SelectorFn) -> SelectorFn:
        if name in _REGISTRY:
            raise ValueError(f"Selector already registered: {name}")
        _REGISTRY[name] = fn
        return fn

    return decorator


def resolve_selector(name: str) -> SelectorFn:
    """Return the registered selector function for ``name``."""
    if name not in _REGISTRY:
        raise UnknownSelectorError(name)
    return _REGISTRY[name]


def list_selectors() -> list[str]:
    """Return all registered selector names."""
    return list(_REGISTRY.keys())


# Import wrappers for side-effect registration.
from src.ashare.strategies.local_select import _local_select_selector
from src.ashare.strategies.multi_factor import _multi_factor_selector
