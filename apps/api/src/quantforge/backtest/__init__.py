"""Backtest layer: execution eligibility, strictly separate from research outcomes.

This package answers one narrow question: *when could a generated signal
defensibly become an executable decision?* It compares the signal's
``observed_at`` against a real session-open instant supplied by
:mod:`quantforge.market`. It does not answer "what trade should be placed" (no
BUY/SELL/LONG/SHORT mapping), reads no price, and simulates no position, order,
fill or P&L -- those are later slices.

RESEARCH vs. BACKTEST
------------------------
::

    RESEARCH:
        earnings observation -> signal -> event-study outcomes -> evaluation/analytics

    BACKTEST:
        historically available signal + session clock -> candidate execution
        session/price type -> (future) fill price -> (future) position/P&L

Research alignment (``effective_session``) is a calendar-mapping convention for
measuring an aftermath; it is not proof a trade could execute. See
:mod:`quantforge.backtest.execution` for the rules.

No persistence, no migration, no API, no UI in this package yet.
"""

from __future__ import annotations

from quantforge.backtest.contracts import (
    ExecutionEligibility,
    ExecutionEligibilityStatus,
    ExecutionPriceType,
)
from quantforge.backtest.execution import assess_execution_eligibility

__all__ = [
    "ExecutionEligibility",
    "ExecutionEligibilityStatus",
    "ExecutionPriceType",
    "assess_execution_eligibility",
]
