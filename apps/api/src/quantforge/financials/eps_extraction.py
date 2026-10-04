"""Read one accession's stored facts and hand them to the pure EPS selectors.

Retrieval only: every accounting rule lives in :mod:`quantforge.financials.eps` (current EPS) and
:mod:`quantforge.financials.comparative_eps` (comparative EPS). This adapter resolves the stored
filing, fetches the facts of the requested concept's tag for that one filing, and maps them to the
selectors' input. It performs no cross-accession lookup (no prior year, no amendment, no
"latest"), reads no acceptance or ingestion time, and writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from quantforge.financials.eps import (
    EPS_CONCEPT_TAG,
    EpsFactObservation,
    EpsInputFact,
    EpsKind,
    EpsPeriodClass,
    EpsStatus,
    select_eps_fact,
)
from quantforge.models.company import CIK_REGEX
from quantforge.models.enums import SecFormType
from quantforge.models.filing import ACCESSION_REGEX
from quantforge.repositories.financial_fact import FinancialFactRepository

__all__ = ["ResolvedEpsInputs", "extract_eps_observation", "resolve_eps_inputs"]


@dataclass(frozen=True, slots=True)
class ResolvedEpsInputs:
    """What the pure selectors need for one accession, read from the database."""

    accession_number: str
    company_cik: str | None
    form: SecFormType
    submission_period_ends: tuple[date, ...]
    facts: tuple[EpsInputFact, ...]


def resolve_eps_inputs(
    session: Session,
    accession_number: str,
    *,
    period_class: EpsPeriodClass,
    eps_kind: EpsKind,
    company_cik: str | None = None,
) -> ResolvedEpsInputs | EpsFactObservation:
    """The selector inputs of ``accession_number``, or a refusal when no single filing resolves.

    ``company_cik`` optionally qualifies WHICH stored filing (``(company, accession)``) is meant;
    it is identity resolution only and never reaches the selectors. Without it the accession alone
    resolves, exactly as before (a joint accession is ``AMBIGUOUS``). With it, only that
    registrant's filing is considered: if none matches, the result is ``NOT_INGESTED`` carrying the
    requested CIK, never another registrant's filing. That refusal's ``company_cik`` is the REQUEST echoed
    back (its ``form`` is ``None``), not evidence that a registrant was found.

    The refusal is a current-EPS observation (``NOT_INGESTED`` / ``AMBIGUOUS``) because nothing is
    stored that a selector could read. Facts are fetched by the requested concept's exact tag for
    this one filing only; every returned row is verified to be bound to that filing.
    """
    if not ACCESSION_REGEX.fullmatch(accession_number):
        message = f"accession_number must look like 0000320193-24-000123, got {accession_number!r}"
        raise ValueError(message)
    if company_cik is not None and not CIK_REGEX.fullmatch(company_cik):
        message = f"company_cik must be a ten-digit CIK, got {company_cik!r}"
        raise ValueError(message)
    repository = FinancialFactRepository(session)
    contexts = repository.filing_fact_contexts(accession_number, company_cik=company_cik)
    ingested = [c for c in contexts if c.submission_period_ends]

    def refuse(status: EpsStatus, reason: str) -> EpsFactObservation:
        return EpsFactObservation.refusal(
            accession_number=accession_number,
            company_cik=company_cik,
            form=None,
            period_class=period_class,
            eps_kind=eps_kind,
            status=status,
            reason=reason,
        )

    if not contexts:
        scope = (
            accession_number if company_cik is None else f"{accession_number} and CIK {company_cik}"
        )
        return refuse(
            EpsStatus.NOT_INGESTED,
            f"no stored SecFiling for {scope}; this is not evidence the source states no EPS",
        )
    if len(ingested) > 1:
        return refuse(
            EpsStatus.AMBIGUOUS,
            f"{accession_number} is stored under {len(ingested)} filings that each have an "
            "ingested submission; the filing identity is not unique",
        )
    if not ingested and len(contexts) > 1:
        return refuse(
            EpsStatus.NOT_INGESTED,
            f"{accession_number} is stored under {len(contexts)} filings but none has ingested "
            "facts; this is not evidence the source states no EPS",
        )
    selected = ingested[0] if ingested else contexts[0]
    facts = repository.facts_for_filing_tag(selected.sec_filing_id, EPS_CONCEPT_TAG[eps_kind])
    for fact in facts:
        if fact.sec_filing_id != selected.sec_filing_id:  # a repository regression, not data
            message = f"fact {fact.id} is bound to another filing than {accession_number}"
            raise RuntimeError(message)
    return ResolvedEpsInputs(
        accession_number=selected.accession_number,
        company_cik=selected.company_cik,
        form=selected.form,
        submission_period_ends=selected.submission_period_ends,
        facts=tuple(
            EpsInputFact(
                fact_id=fact.id,
                accession_number=selected.accession_number,
                tag=fact.tag,
                taxonomy_version=fact.version,
                unit=fact.unit,
                period_end=fact.period_end,
                duration_quarters=fact.duration_quarters,
                value=fact.value,
                segments=fact.segments,
                coreg=fact.coreg,
            )
            for fact in facts
        ),
    )


def extract_eps_observation(
    session: Session,
    accession_number: str,
    *,
    period_class: EpsPeriodClass,
    eps_kind: EpsKind,
    company_cik: str | None = None,
) -> EpsFactObservation:
    """The EPS observation of ``accession_number`` for the explicitly requested class and kind.

    ``NOT_INGESTED`` means QuantForge has no stored facts for the accession; it says nothing about
    what the source contains. An amendment is its own accession and is never preferred or merged.
    """
    resolved = resolve_eps_inputs(
        session,
        accession_number,
        period_class=period_class,
        eps_kind=eps_kind,
        company_cik=company_cik,
    )
    if isinstance(resolved, EpsFactObservation):
        return resolved
    return select_eps_fact(
        accession_number=resolved.accession_number,
        company_cik=resolved.company_cik,
        form=resolved.form,
        submission_period_ends=resolved.submission_period_ends,
        facts=resolved.facts,
        period_class=period_class,
        eps_kind=eps_kind,
    )
