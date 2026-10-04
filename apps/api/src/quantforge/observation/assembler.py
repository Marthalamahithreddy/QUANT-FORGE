"""Assemble a :class:`ResearchObservation` from stored evidence (Step 10.8A). Read-only.

The assembler COORDINATES; it owns no financial semantics. Every number comes from an existing
layer: :func:`~quantforge.research.event_study.analyze_earnings_event` (alignment, returns,
benchmark, abnormal, EPS surprise, availability) and the 10.6 EPS stack
(:func:`~quantforge.financials.comparative_eps_extraction.extract_comparative_eps_observation` then
:func:`~quantforge.financials.eps_change.compute_eps_change`). It performs no price division, no
return subtraction, no session arithmetic, no provider call and no write.

Two mode-specific event-study calls are composed from above with identical pinned inputs, because
the engine takes one mode per call: DESCRIPTIVE always; POINT_IN_TIME only when the existing
availability policy is eligible (a refused policy would withhold every session field anyway, so the
call would repeat reads to learn nothing). A refused PIT section therefore never affects the
descriptive one.

A filing is linked ONLY when the caller supplies its accession; nothing is inferred from the event,
its period or nearby filings. A link is consistent when the stored filing belongs to the event's
company; the fiscal period is never verified (``PERIOD_UNVERIFIED``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from quantforge.financials.comparative_eps_extraction import extract_comparative_eps_observation
from quantforge.financials.eps import EpsKind, EpsPeriodClass
from quantforge.financials.eps_change import compute_eps_change
from quantforge.models.earnings import EarningsEvent
from quantforge.models.filing import ACCESSION_REGEX
from quantforge.observation.models import (
    AccountingSection,
    AccountingState,
    DescriptiveSection,
    DisclosureEvidence,
    DisclosureEvidenceLevel,
    EventSection,
    FilingLink,
    FilingLinkStatus,
    ObservationIdentity,
    ObservationParameters,
    ObservationProvenance,
    PeriodVerification,
    PointInTimeSection,
    ResearchObservation,
    derive_disclosure_evidence_level,
)
from quantforge.repositories.filing import StoredFilingRef, StoredFilingRepository
from quantforge.repositories.financial_fact import FinancialFactRepository
from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
)
from quantforge.research.event_returns import DEFAULT_HORIZONS
from quantforge.research.event_study import EventStudyResult, analyze_earnings_event

__all__ = ["EarningsEventNotFoundError", "assemble_research_observation"]


class EarningsEventNotFoundError(LookupError):
    """The requested earnings event id is not stored."""


def _resolve_link(
    session: Session, event: EarningsEvent, accession: str | None
) -> tuple[FilingLink, StoredFilingRef | None]:
    cik = event.company.cik
    if accession is None:
        return (
            FilingLink(
                status=FilingLinkStatus.NOT_LINKED,
                period_verification=PeriodVerification.NOT_APPLICABLE,
                accession_number=None,
                company_cik=cik,
                form=None,
                reason="no filing accession was supplied; no filing is inferred from the event",
            ),
            None,
        )
    stored = StoredFilingRepository(session).for_accession(accession)
    if not stored:
        return (
            FilingLink(
                status=FilingLinkStatus.FILING_NOT_STORED,
                period_verification=PeriodVerification.NOT_APPLICABLE,
                accession_number=accession,
                company_cik=cik,
                form=None,
                reason=f"no stored filing has accession {accession}; nothing is fetched",
            ),
            None,
        )
    own = [row for row in stored if row.company_id == event.company_id]
    if cik is None:
        own = []  # consistency cannot be established, and extraction could not be CIK-qualified
    if not own:
        return (
            FilingLink(
                status=FilingLinkStatus.CIK_MISMATCH_REFUSED,
                period_verification=PeriodVerification.NOT_APPLICABLE,
                accession_number=accession,
                company_cik=cik,
                form=None,
                reason=(
                    f"accession {accession} is not stored under this event's company; "
                    "or the event's company has no CIK; the filing is not read and nothing "
                    "is borrowed from another registrant"
                ),
            ),
            None,
        )
    row = own[0]  # unique (company, accession)
    return (
        FilingLink(
            status=FilingLinkStatus.CALLER_SUPPLIED_CIK_CONSISTENT,
            period_verification=PeriodVerification.PERIOD_UNVERIFIED,
            accession_number=accession,
            company_cik=cik,
            form=row.form,
            reason=(
                "the caller-supplied filing belongs to the event's company; whether it covers "
                "the event's fiscal period is not verified"
            ),
        ),
        row,
    )


def _fact_digests(
    session: Session, fact_ids: Sequence[tuple[str, uuid.UUID | None]]
) -> tuple[tuple[str, str], ...]:
    repository = FinancialFactRepository(session)
    digests: list[tuple[str, str]] = []
    for role, fact_id in fact_ids:
        if fact_id is None:
            continue
        trace = repository.trace(fact_id)
        if trace is None:  # a selected fact always has its artifact (NOT NULL FKs)
            message = f"the {role} fact {fact_id} has no resolvable artifact"
            raise RuntimeError(message)
        digests.append((role, trace.artifact_sha256))
    return tuple(digests)


def assemble_research_observation(  # noqa: PLR0913 - one explicit knob per independent research choice
    session: Session,
    earnings_event_id: uuid.UUID,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    security_id: uuid.UUID | None = None,
    stock_source_id: uuid.UUID | None = None,
    benchmark_id: uuid.UUID | None = None,
    benchmark_source_id: uuid.UUID | None = None,
    filing_accession: str | None = None,
    eps_period_class: EpsPeriodClass | None = None,
    eps_kind: EpsKind | None = None,
) -> ResearchObservation:
    """Assemble one observation for an explicitly selected event and explicit configuration.

    Raises:
        EarningsEventNotFoundError: the event id is not stored.
        ValueError: invalid horizons, a malformed accession, or an accession without an explicit
            ``eps_period_class`` and ``eps_kind`` (never defaulted or inferred).
    """
    if filing_accession is not None and not ACCESSION_REGEX.fullmatch(filing_accession):
        message = f"filing_accession must look like 0000320193-24-000123, got {filing_accession!r}"
        raise ValueError(message)
    parameters = ObservationParameters(
        horizons=tuple(horizons),
        security_id=security_id,
        stock_source_id=stock_source_id,
        benchmark_id=benchmark_id,
        benchmark_source_id=benchmark_source_id,
        filing_accession=filing_accession,
        eps_period_class=eps_period_class,
        eps_kind=eps_kind,
    )
    event = session.get(EarningsEvent, earnings_event_id)
    if event is None:
        message = f"no earnings event {earnings_event_id}"
        raise EarningsEventNotFoundError(message)

    def study(mode: ResearchMode) -> EventStudyResult:
        # identical pinned inputs for both modes
        return analyze_earnings_event(
            session,
            event,
            mode=mode,
            security_id=parameters.security_id,
            horizons=parameters.horizons,
            stock_source_id=parameters.stock_source_id,
            benchmark_id=parameters.benchmark_id,
            benchmark_source_id=parameters.benchmark_source_id,
        )

    descriptive = study(ResearchMode.DESCRIPTIVE)
    eligibility = classify_point_in_time_eligibility(
        ResearchMode.POINT_IN_TIME,
        observed_at=event.observed_at,
        observed_at_basis=event.observed_at_basis,
    )
    pit_study = (
        study(ResearchMode.POINT_IN_TIME)
        if eligibility is PointInTimeEligibility.ELIGIBLE
        else None
    )

    link, filing = _resolve_link(session, event, filing_accession)
    evidence = DisclosureEvidence(DisclosureEvidenceLevel.NONE, None, None, None)
    if link.status is FilingLinkStatus.NOT_LINKED or filing is None:
        accounting = AccountingSection(
            AccountingState.NOT_REQUESTED
            if link.status is FilingLinkStatus.NOT_LINKED
            else AccountingState.LINK_REFUSED
        )
        digests: tuple[tuple[str, str], ...] = ()
    else:
        assert filing_accession is not None
        assert eps_period_class is not None
        assert eps_kind is not None
        evidence = DisclosureEvidence(
            derive_disclosure_evidence_level(filing.form, filing.items),
            filing.accession_number,
            filing.form,
            filing.items,
        )
        comparative = extract_comparative_eps_observation(
            session,
            filing_accession,
            period_class=eps_period_class,
            eps_kind=eps_kind,
            company_cik=event.company.cik,
        )
        change = compute_eps_change(comparative)
        accounting = AccountingSection(AccountingState.EVALUATED, change)
        comparative_fact = comparative.comparative_fact
        digests = _fact_digests(
            session,
            [
                ("current", comparative.current.fact_id),
                ("comparative", comparative_fact.fact_id if comparative_fact else None),
            ],
        )

    return ResearchObservation(
        identity=ObservationIdentity(
            company_id=event.company_id,
            company_cik=event.company.cik,
            event_id=event.id,
            listing_resolution=descriptive.alignment.listing_resolution,
            benchmark_id=benchmark_id,
            filing_accession=filing_accession,
        ),
        parameters=parameters,
        event=EventSection(
            event_id=event.id,
            fingerprint=event.fingerprint,
            fiscal_year=event.fiscal_year,
            fiscal_quarter=event.fiscal_quarter,
            event_date=event.event_date,
            event_timestamp=event.event_timestamp,
            timing=event.timing,
            reported_eps=event.reported_eps,
            estimated_eps=event.estimated_eps,
            observed_at=event.observed_at,
            observed_at_basis=event.observed_at_basis,
        ),
        filing_link=link,
        disclosure_evidence=evidence,
        accounting=accounting,
        descriptive=DescriptiveSection(descriptive),
        point_in_time=PointInTimeSection(eligibility, pit_study),
        provenance=ObservationProvenance(
            event_fingerprint=event.fingerprint,
            event_source_id=event.source_id,
            event_ingestion_run_id=event.ingestion_run_id,
            stock_price_source_id=descriptive.stock_returns.price_source_id,
            benchmark_id=benchmark_id,
            filing_source_id=filing.source_id if filing is not None else None,
            filing_ingestion_run_id=filing.ingestion_run_id if filing is not None else None,
            accounting_artifact_sha256=digests,
        ),
    )
