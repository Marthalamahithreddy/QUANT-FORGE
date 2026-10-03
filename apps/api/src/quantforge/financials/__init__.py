"""Semantic financial layer: interpretations over stored, accession-bound financial facts.

This package sits *above* :mod:`quantforge.repositories.financial_fact` (which only retrieves
facts) and *below* anything that would compare or act on a figure. Today it holds one capability:
accession-scoped EPS selection (:mod:`quantforge.financials.eps`), a pure function plus a thin
database adapter (:mod:`quantforge.financials.eps_extraction`).

It is descriptive accounting extraction: it never states when a fact was known, never reads a
filing's acceptance time, never looks at another accession, and has no signal, growth, surprise,
return or execution concept. It persists nothing and adds no migration.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quantforge.financials.eps import (
        EPS_CONCEPT_TAG,
        EPS_UNIT,
        EpsFactObservation,
        EpsInputFact,
        EpsKind,
        EpsPeriodClass,
        EpsStatus,
        select_eps_fact,
    )
    from quantforge.financials.eps_extraction import extract_eps_observation

# Lazy (PEP 562), like the other domain packages: importing the pure selector must not load the
# database adapter or its repositories.
_EXPORTS: dict[str, str] = {
    "EPS_CONCEPT_TAG": "quantforge.financials.eps",
    "EPS_UNIT": "quantforge.financials.eps",
    "EpsFactObservation": "quantforge.financials.eps",
    "EpsInputFact": "quantforge.financials.eps",
    "EpsKind": "quantforge.financials.eps",
    "EpsPeriodClass": "quantforge.financials.eps",
    "EpsStatus": "quantforge.financials.eps",
    "extract_eps_observation": "quantforge.financials.eps_extraction",
    "select_eps_fact": "quantforge.financials.eps",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        message = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(message)
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_EXPORTS])


__all__ = [
    "EPS_CONCEPT_TAG",
    "EPS_UNIT",
    "EpsFactObservation",
    "EpsInputFact",
    "EpsKind",
    "EpsPeriodClass",
    "EpsStatus",
    "extract_eps_observation",
    "select_eps_fact",
]
