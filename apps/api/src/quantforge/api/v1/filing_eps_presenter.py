"""Pure mapping from the financial-domain observations to the public response schema.

No query, no mutation of an observation and no financial calculation: every value, status, reason
and transition is copied from the domain objects, which remain the single implementation of the EPS
semantics.
"""

from __future__ import annotations

from quantforge.financials.comparative_eps import ComparativeEpsObservation
from quantforge.financials.eps import EpsFactObservation, EpsInputFact
from quantforge.financials.eps_change import EpsChangeObservation
from quantforge.schemas.filing_eps import (
    ComparativeEpsSection,
    CurrentEpsSection,
    EpsChangeSection,
    FactEvidence,
    FilingEpsResponse,
)

__all__ = ["present_filing_eps"]


def _evidence_from_input(fact: EpsInputFact) -> FactEvidence:
    return FactEvidence(
        fact_id=fact.fact_id,
        tag=fact.tag,
        taxonomy_version=fact.taxonomy_version,
        unit=fact.unit,
        period_end=fact.period_end,
        qtrs=fact.duration_quarters,
        value=fact.value,
        segments=fact.segments,
        coreg=fact.coreg,
    )


def _evidence_from_current(current: EpsFactObservation) -> FactEvidence | None:
    """The selected fact of a current observation (``None`` for a refusal that selected none).

    The domain guarantees a fact's fields are all present or all absent; a half-present fact is a
    broken invariant and fails loudly rather than silently dropping evidence.
    """
    if current.fact_id is None:
        return None
    if (
        current.tag is None
        or current.taxonomy_version is None
        or current.unit is None
        or current.period_end is None
        or current.duration_quarters is None
        or current.segments is None
        or current.coreg is None
    ):
        message = "the current observation carries a partial fact"
        raise RuntimeError(message)
    return FactEvidence(
        fact_id=current.fact_id,
        tag=current.tag,
        taxonomy_version=current.taxonomy_version,
        unit=current.unit,
        period_end=current.period_end,
        qtrs=current.duration_quarters,
        value=current.value,
        segments=current.segments,
        coreg=current.coreg,
    )


def present_filing_eps(
    comparative: ComparativeEpsObservation, change: EpsChangeObservation
) -> FilingEpsResponse:
    """The public response for one filing's current EPS, comparative EPS and EPS change."""
    if change.comparative is not comparative:
        message = "the EPS change must have been computed from this comparative observation"
        raise ValueError(message)
    current = comparative.current
    return FilingEpsResponse(
        accession=comparative.accession_number,
        company_cik=comparative.company_cik,
        form=comparative.form,
        period_class=comparative.period_class,
        eps_kind=comparative.eps_kind,
        current=CurrentEpsSection(
            status=current.status, reason=current.reason, fact=_evidence_from_current(current)
        ),
        comparative=ComparativeEpsSection(
            status=comparative.status,
            reason=comparative.reason,
            fact=(
                None
                if comparative.comparative_fact is None
                else _evidence_from_input(comparative.comparative_fact)
            ),
        ),
        change=EpsChangeSection(
            status=change.status,
            eps_change=change.eps_change,
            transition=change.transition,
            percentage_change=change.percentage_change,
            percentage_unavailable_reason=change.percentage_unavailable_reason,
        ),
    )
