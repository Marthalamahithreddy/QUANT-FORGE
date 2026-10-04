"""Research observation layer (Step 10.8A): composes existing research and accounting results.

Sits ABOVE ``research`` and ``financials``; nothing below imports it. Read-only: no provider call,
no write, no signal, no execution, no persistence (that is 10.9).
"""

from __future__ import annotations

from quantforge.observation.assembler import (
    EarningsEventNotFoundError,
    assemble_research_observation,
)
from quantforge.observation.models import ResearchObservation

__all__ = ["EarningsEventNotFoundError", "ResearchObservation", "assemble_research_observation"]
