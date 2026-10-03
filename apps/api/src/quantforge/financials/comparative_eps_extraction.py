"""Read one accession's stored facts and hand them to the pure comparative EPS selector.

Retrieval only, sharing :func:`quantforge.financials.eps_extraction.resolve_eps_inputs` with current
EPS extraction. The repository returns the stored facts of the requested concept for ONE filing; the
domain selector chooses or refuses. There is no SQL "latest/closest prior" shortcut, no other
accession is read (no prior-year filing, no amendment lookup), no acceptance or ingestion time is
read, and nothing is written.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from quantforge.financials.comparative_eps import (
    ComparativeEpsObservation,
    select_comparative_eps,
)
from quantforge.financials.eps import EpsFactObservation, EpsKind, EpsPeriodClass
from quantforge.financials.eps_extraction import resolve_eps_inputs

__all__ = ["extract_comparative_eps_observation"]


def extract_comparative_eps_observation(
    session: Session,
    accession_number: str,
    *,
    period_class: EpsPeriodClass,
    eps_kind: EpsKind,
) -> ComparativeEpsObservation:
    """The comparative EPS "as presented in this filing" for ``accession_number``, or a refusal.

    An accession with no stored filing/facts is ``CURRENT_UNAVAILABLE`` carrying the current
    selector's ``NOT_INGESTED`` (never "missing"). An amendment is its own accession.
    """
    resolved = resolve_eps_inputs(
        session, accession_number, period_class=period_class, eps_kind=eps_kind
    )
    if isinstance(resolved, EpsFactObservation):
        return ComparativeEpsObservation.current_unavailable(
            accession_number=accession_number,
            company_cik=resolved.company_cik,
            form=resolved.form,
            period_class=period_class,
            eps_kind=eps_kind,
            current=resolved,
        )
    return select_comparative_eps(
        accession_number=resolved.accession_number,
        company_cik=resolved.company_cik,
        form=resolved.form,
        submission_period_ends=resolved.submission_period_ends,
        facts=resolved.facts,
        period_class=period_class,
        eps_kind=eps_kind,
    )
