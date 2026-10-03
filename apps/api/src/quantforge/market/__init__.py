"""Market-domain primitives shared by research and backtest: the session clock.

The contracts (:class:`MarketSession`, :class:`SessionLookup`,
:class:`SessionCalendar`) are dependency-free and exported eagerly. The
``exchange_calendars``-backed adapter (:class:`ExchangeCalendarsSessionCalendar`,
:data:`SUPPORTED_MICS`) is exported lazily (PEP 562), so importing this package
-- as :mod:`quantforge.backtest` does -- never loads ``exchange_calendars`` or
pandas. ``tests/test_module_isolation.py`` enforces that in a clean interpreter.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from quantforge.market.corporate_actions import (
    CorporateActionGuardResult,
    CoverageInterval,
    GuardStatus,
    SplitFact,
    evaluate_split_guard,
)
from quantforge.market.sessions import (
    MarketSession,
    SessionCalendar,
    SessionLookup,
    SessionLookupStatus,
)

if TYPE_CHECKING:
    from quantforge.market.calendar import SUPPORTED_MICS, ExchangeCalendarsSessionCalendar

__all__ = [
    "SUPPORTED_MICS",
    "CorporateActionGuardResult",
    "CoverageInterval",
    "ExchangeCalendarsSessionCalendar",
    "GuardStatus",
    "MarketSession",
    "SessionCalendar",
    "SessionLookup",
    "SessionLookupStatus",
    "SplitFact",
    "evaluate_split_guard",
]

_ADAPTER_EXPORTS = ("SUPPORTED_MICS", "ExchangeCalendarsSessionCalendar")


def __getattr__(name: str) -> Any:
    if name in _ADAPTER_EXPORTS:
        value = getattr(importlib.import_module("quantforge.market.calendar"), name)
        globals()[name] = value
        return value
    message = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(message)


def __dir__() -> list[str]:
    return sorted([*globals(), *_ADAPTER_EXPORTS])
