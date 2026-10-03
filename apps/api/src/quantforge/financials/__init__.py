"""Semantic financial layer: interpretations over stored, accession-bound financial facts.

This package sits *above* :mod:`quantforge.repositories.financial_fact` (which only retrieves
facts) and *below* anything that would compare or act on a figure. It holds accession-scoped
EPS selection (:mod:`quantforge.financials.eps`) and same-accession comparative EPS (:mod:`quantforge.financials.comparative_eps`, "comparative EPS as presented in this
filing"), each a pure function plus a thin database adapter
(:mod:`quantforge.financials.eps_extraction`, :mod:`quantforge.financials.comparative_eps_extraction`).

It is descriptive accounting extraction: it never states when a fact was known, never reads a
filing's acceptance time, never looks at another accession, and has no signal, growth, surprise,
return or execution concept. It persists nothing and adds no migration.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quantforge.financials.comparative_eps import (
        ComparativeEpsObservation,
        ComparativeEpsStatus,
        select_comparative_eps,
    )
    from quantforge.financials.comparative_eps_extraction import (
        extract_comparative_eps_observation,
    )
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
    "ComparativeEpsObservation": "quantforge.financials.comparative_eps",
    "ComparativeEpsStatus": "quantforge.financials.comparative_eps",
    "extract_comparative_eps_observation": "quantforge.financials.comparative_eps_extraction",
    "select_comparative_eps": "quantforge.financials.comparative_eps",
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
    "ComparativeEpsObservation",
    "ComparativeEpsStatus",
    "EpsFactObservation",
    "EpsInputFact",
    "EpsKind",
    "EpsPeriodClass",
    "EpsStatus",
    "extract_comparative_eps_observation",
    "extract_eps_observation",
    "select_comparative_eps",
    "select_eps_fact",
]
