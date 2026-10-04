"""The research observation domain object (Step 10.8A): pure, frozen, no database, no arithmetic.

A :class:`ResearchObservation` answers: for one explicitly selected earnings event and one explicit
research configuration, what descriptive accounting and market evidence can be assembled, and what
point-in-time conclusions are or are not allowed. It is not a signal, forecast, score or rating.

It COMPOSES results the existing layers already own (the Step 8 event study, the 10.6 EPS stack);
it computes nothing itself. Sections are independent: a refused point-in-time section, an
unavailable accounting section or a refused filing link never blank the descriptive section.
Statuses are carried from the originating layers; this module only adds the few aggregation-level
states that do not exist below it (filing link, disclosure evidence level, section states).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from quantforge.financials.comparative_eps import ComparativeEpsStatus
from quantforge.financials.eps import EpsKind, EpsPeriodClass, EpsStatus
from quantforge.financials.eps_change import EpsChangeObservation
from quantforge.models.enums import (
    RESEARCH_PRICE_BASIS,
    AvailabilityBasis,
    EarningsTiming,
    PriceBasis,
    SecFormType,
)
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturnStatus
from quantforge.research.calendar import AlignmentStatus
from quantforge.research.eps_surprise import EpsSurpriseStatus
from quantforge.research.event_study import EventStudyResult
from quantforge.research.listing_resolution import ListingResolution, ListingResolutionStatus
from quantforge.research.returns import ForwardReturnStatus, validate_horizons

__all__ = [
    "KNOWN_PROVENANCE_GAPS",
    "AccountingSection",
    "AccountingState",
    "DescriptiveSection",
    "DisclosureEvidence",
    "DisclosureEvidenceLevel",
    "EventSection",
    "FilingLink",
    "FilingLinkStatus",
    "ObservationIdentity",
    "ObservationParameters",
    "ObservationProvenance",
    "ObservationRefusal",
    "PeriodVerification",
    "PointInTimeSection",
    "PointInTimeState",
    "RefusalScope",
    "ResearchObservation",
    "SectionState",
    "derive_disclosure_evidence_level",
]

#: Provenance that does not exist yet (10.9); named so its absence is never mistaken for coverage.
KNOWN_PROVENANCE_GAPS: Final[tuple[str, ...]] = (
    "NO_UNIFIED_INPUT_MANIFEST",
    "BENCHMARK_BARS_HAVE_NO_INGESTION_RUN_ID",
    "NO_METHODOLOGY_VERSION",
    "NO_CALENDAR_VERSION",
    "OBSERVATION_NOT_PERSISTED",
    "NO_EVENT_FILING_LINK_RELATION_STORED",
)

_ITEM_2_02: Final = "2.02"
_EIGHT_K_FORMS: Final = frozenset({SecFormType.FORM_8K, SecFormType.FORM_8K_A})


class RefusalScope(StrEnum):
    EVENT = "EVENT"
    MARKET = "MARKET"
    BENCHMARK = "BENCHMARK"
    FILING_LINK = "FILING_LINK"
    ACCOUNTING = "ACCOUNTING"
    POINT_IN_TIME = "POINT_IN_TIME"


@dataclass(frozen=True, slots=True)
class ObservationRefusal:
    """One reason part of the observation is unavailable, naming the originating status."""

    scope: RefusalScope
    code: str
    reason: str
    horizon: int | None = None


class FilingLinkStatus(StrEnum):
    #: No accession was supplied. Nothing is inferred from the event, the period or any nearby filing.
    NOT_LINKED = "NOT_LINKED"
    #: The caller supplied an accession and no stored filing has it. Never fetched.
    FILING_NOT_STORED = "FILING_NOT_STORED"
    #: The accession is stored, but none of its registrants is the event's company.
    CIK_MISMATCH_REFUSED = "CIK_MISMATCH_REFUSED"
    #: The caller-supplied accession is stored under the event's own company (company/CIK equality
    #: only; it says nothing about which fiscal period the filing covers).
    CALLER_SUPPLIED_CIK_CONSISTENT = "CALLER_SUPPLIED_CIK_CONSISTENT"


class PeriodVerification(StrEnum):
    """There is no VERIFIED member: no explicit safe event<->filing period relationship exists."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    PERIOD_UNVERIFIED = "PERIOD_UNVERIFIED"


class DisclosureEvidenceLevel(StrEnum):
    """Evidence CATEGORIES from stored filing metadata, not a ladder and never an availability time."""

    NONE = "NONE"
    #: A stored filing exists (D0).
    D0_FILING_EXISTS = "D0_FILING_EXISTS"
    #: A stored 8-K / 8-K/A whose items list explicitly includes Item 2.02 (D1).
    D1_ITEM_2_02_DECLARED = "D1_ITEM_2_02_DECLARED"


def derive_disclosure_evidence_level(
    form: SecFormType, items: str | None
) -> DisclosureEvidenceLevel:
    """D1 iff an 8-K family form lists the exact item token ``2.02``; otherwise D0."""
    if form in _EIGHT_K_FORMS and items is not None:
        tokens = {token.strip() for token in items.split(",")}
        if _ITEM_2_02 in tokens:
            return DisclosureEvidenceLevel.D1_ITEM_2_02_DECLARED
    return DisclosureEvidenceLevel.D0_FILING_EXISTS


class SectionState(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class AccountingState(StrEnum):
    #: No filing was linked; nothing was evaluated.
    NOT_REQUESTED = "NOT_REQUESTED"
    #: A filing was supplied but the link was refused; the filing was not read for accounting.
    LINK_REFUSED = "LINK_REFUSED"
    #: The existing EPS stack ran; its own statuses (including refusals) are in ``change``.
    EVALUATED = "EVALUATED"


class PointInTimeState(StrEnum):
    #: The existing availability policy refused; no PIT study is carried.
    REFUSED = "REFUSED"
    #: Availability evidence passed the existing policy; the PIT-mode study is carried whole
    #: (its own horizon statuses may still refuse).
    EVIDENCE_ELIGIBLE = "EVIDENCE_ELIGIBLE"


@dataclass(frozen=True, slots=True)
class ObservationParameters:
    """Every effective input. Defaults are the existing research defaults, visible here."""

    horizons: tuple[int, ...]
    price_basis: PriceBasis = RESEARCH_PRICE_BASIS
    security_id: uuid.UUID | None = None
    stock_source_id: uuid.UUID | None = None
    benchmark_id: uuid.UUID | None = None
    benchmark_source_id: uuid.UUID | None = None
    filing_accession: str | None = None
    eps_period_class: EpsPeriodClass | None = None
    eps_kind: EpsKind | None = None

    def __post_init__(self) -> None:
        validate_horizons(self.horizons)
        if self.price_basis is not RESEARCH_PRICE_BASIS:
            message = "research reads only the research price basis"
            raise ValueError(message)
        accounting = (self.eps_period_class, self.eps_kind)
        if self.filing_accession is None and any(x is not None for x in accounting):
            message = "eps_period_class/eps_kind are only meaningful with a filing_accession"
            raise ValueError(message)
        if self.filing_accession is not None and any(x is None for x in accounting):
            message = "a filing_accession requires an explicit eps_period_class and eps_kind"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ObservationIdentity:
    company_id: uuid.UUID
    company_cik: str | None
    event_id: uuid.UUID
    listing_resolution: ListingResolution
    benchmark_id: uuid.UUID | None
    filing_accession: str | None


@dataclass(frozen=True, slots=True)
class EventSection:
    """The selected vendor observation exactly as stored (never re-selected or re-interpreted)."""

    event_id: uuid.UUID
    fingerprint: str
    fiscal_year: int
    fiscal_quarter: int | None
    event_date: date
    event_timestamp: datetime | None
    timing: EarningsTiming
    reported_eps: Decimal | None
    estimated_eps: Decimal | None
    observed_at: datetime | None
    observed_at_basis: AvailabilityBasis | None


@dataclass(frozen=True, slots=True)
class FilingLink:
    status: FilingLinkStatus
    period_verification: PeriodVerification
    accession_number: str | None
    #: The event company's CIK, as stored (may be absent).
    company_cik: str | None
    #: Present only for a consistent link; a refused foreign filing is never described.
    form: SecFormType | None
    reason: str

    def __post_init__(self) -> None:
        consistent = self.status is FilingLinkStatus.CALLER_SUPPLIED_CIK_CONSISTENT
        if (self.status is FilingLinkStatus.NOT_LINKED) != (self.accession_number is None):
            message = "an accession is present exactly when a link was requested"
            raise ValueError(message)
        if (self.period_verification is PeriodVerification.PERIOD_UNVERIFIED) != consistent:
            message = "PERIOD_UNVERIFIED applies exactly to a consistent link"
            raise ValueError(message)
        if (self.form is not None) != consistent:
            message = "a filing form is carried only for a consistent link"
            raise ValueError(message)
        if not self.reason:
            message = "a filing link must state a reason"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class DisclosureEvidence:
    """D0/D1 from stored filing metadata only. There is deliberately no time field."""

    level: DisclosureEvidenceLevel
    accession_number: str | None
    form: SecFormType | None
    items: str | None

    def __post_init__(self) -> None:
        if self.level is DisclosureEvidenceLevel.NONE:
            if self.accession_number or self.form or self.items:
                message = "no evidence carries no filing"
                raise ValueError(message)
            return
        if self.accession_number is None or self.form is None:
            message = "filing evidence needs the accession and form"
            raise ValueError(message)
        if derive_disclosure_evidence_level(self.form, self.items) is not self.level:
            message = "the evidence level contradicts the stored form/items"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class AccountingSection:
    state: AccountingState
    change: EpsChangeObservation | None = None

    def __post_init__(self) -> None:
        if (self.state is AccountingState.EVALUATED) != (self.change is not None):
            message = "an EPS change is carried exactly when accounting was evaluated"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class DescriptiveSection:
    """The DESCRIPTIVE-mode event study, whole."""

    study: EventStudyResult

    def __post_init__(self) -> None:
        if self.study.research_mode is not ResearchMode.DESCRIPTIVE:
            message = "the descriptive section carries a DESCRIPTIVE study"
            raise ValueError(message)

    @property
    def state(self) -> SectionState:
        statuses = [fr.status for fr in self.study.stock_returns.forward_returns]
        available = sum(1 for status in statuses if status is ForwardReturnStatus.AVAILABLE)
        if available == 0:
            return SectionState.UNAVAILABLE
        return SectionState.AVAILABLE if available == len(statuses) else SectionState.PARTIAL


@dataclass(frozen=True, slots=True)
class PointInTimeSection:
    eligibility: PointInTimeEligibility
    study: EventStudyResult | None = None

    def __post_init__(self) -> None:
        if self.eligibility is PointInTimeEligibility.NOT_APPLICABLE:
            message = "the point-in-time section is always evaluated in POINT_IN_TIME mode"
            raise ValueError(message)
        eligible = self.eligibility is PointInTimeEligibility.ELIGIBLE
        if eligible != (self.study is not None):
            message = "a PIT study is carried exactly when availability evidence is eligible"
            raise ValueError(message)
        if self.study is not None and (
            self.study.research_mode is not ResearchMode.POINT_IN_TIME
            or self.study.point_in_time_eligibility is not PointInTimeEligibility.ELIGIBLE
        ):
            message = "the carried PIT study must be an ELIGIBLE POINT_IN_TIME study"
            raise ValueError(message)

    @property
    def state(self) -> PointInTimeState:
        if self.eligibility is PointInTimeEligibility.ELIGIBLE:
            return PointInTimeState.EVIDENCE_ELIGIBLE
        return PointInTimeState.REFUSED


@dataclass(frozen=True, slots=True)
class ObservationProvenance:
    """Existing references only; anything not yet recorded is named in ``known_gaps``."""

    event_fingerprint: str
    event_source_id: uuid.UUID
    event_ingestion_run_id: uuid.UUID | None
    stock_price_source_id: uuid.UUID | None
    benchmark_id: uuid.UUID | None
    filing_source_id: uuid.UUID | None = None
    filing_ingestion_run_id: uuid.UUID | None = None
    #: FSDS package digests of the facts the accounting section used, by role.
    accounting_artifact_sha256: tuple[tuple[str, str], ...] = ()
    known_gaps: tuple[str, ...] = field(default=KNOWN_PROVENANCE_GAPS)


@dataclass(frozen=True, slots=True)
class ResearchObservation:
    identity: ObservationIdentity
    parameters: ObservationParameters
    event: EventSection
    filing_link: FilingLink
    disclosure_evidence: DisclosureEvidence
    accounting: AccountingSection
    descriptive: DescriptiveSection
    point_in_time: PointInTimeSection
    provenance: ObservationProvenance

    def __post_init__(self) -> None:
        link = self.filing_link
        consistent = link.status is FilingLinkStatus.CALLER_SUPPLIED_CIK_CONSISTENT
        if link.accession_number != self.parameters.filing_accession:
            message = "the filing link does not match the requested accession"
            raise ValueError(message)
        if consistent:
            if self.disclosure_evidence.level is DisclosureEvidenceLevel.NONE:
                message = "a consistent link carries at least D0 evidence"
                raise ValueError(message)
            if self.accounting.state is not AccountingState.EVALUATED:
                message = "a consistent link evaluates accounting"
                raise ValueError(message)
        else:
            if self.disclosure_evidence.level is not DisclosureEvidenceLevel.NONE:
                message = "evidence requires a consistent link"
                raise ValueError(message)
            expected = (
                AccountingState.NOT_REQUESTED
                if link.status is FilingLinkStatus.NOT_LINKED
                else AccountingState.LINK_REFUSED
            )
            if self.accounting.state is not expected:
                message = f"accounting must be {expected} for link {link.status}"
                raise ValueError(message)
        study = self.descriptive.study
        mismatches = (
            self.identity.filing_accession != self.parameters.filing_accession,
            self.identity.benchmark_id != self.parameters.benchmark_id,
            study.benchmark_id != self.parameters.benchmark_id,
            self.provenance.benchmark_id != self.parameters.benchmark_id,
            self.identity.company_id != study.company_id,
            self.identity.listing_resolution != study.alignment.listing_resolution,
            len(
                {self.event.fingerprint, self.provenance.event_fingerprint, study.event_fingerprint}
            )
            != 1,
        )
        if any(mismatches):
            message = "identity, parameters, provenance and the study must agree"
            raise ValueError(message)
        event_ids = {
            self.identity.event_id,
            self.event.event_id,
            self.descriptive.study.event_id,
        }
        if len(event_ids) != 1:
            message = "every section must describe the same event"
            raise ValueError(message)
        if self.point_in_time.study is not None and (
            self.point_in_time.study.event_id != self.identity.event_id
        ):
            message = "the PIT study describes a different event"
            raise ValueError(message)

    @property
    def refusals(self) -> tuple[ObservationRefusal, ...]:
        """Every unavailable part, scoped and coded by the originating layer's own status."""
        return _collect_refusals(self)


def _pit_study_refusals(study: EventStudyResult) -> list[ObservationRefusal]:
    """Horizon-level refusals inside an evidence-eligible PIT study, scoped POINT_IN_TIME."""
    out: list[ObservationRefusal] = []
    alignment = study.alignment.session_alignment
    if alignment is not None and alignment.status is not AlignmentStatus.ALIGNED:
        out.append(
            ObservationRefusal(RefusalScope.POINT_IN_TIME, alignment.status.value, alignment.reason)
        )
    for forward in study.stock_returns.forward_returns:
        if forward.status not in (ForwardReturnStatus.AVAILABLE, ForwardReturnStatus.NO_ALIGNMENT):
            out.append(
                ObservationRefusal(
                    RefusalScope.POINT_IN_TIME,
                    forward.status.value,
                    forward.reason,
                    forward.horizon,
                )
            )
    return out


def _collect_refusals(observation: ResearchObservation) -> tuple[ObservationRefusal, ...]:  # noqa: PLR0912
    out: list[ObservationRefusal] = []
    study = observation.descriptive.study
    surprise = study.eps_surprise
    if surprise.status is not EpsSurpriseStatus.AVAILABLE:
        out.append(ObservationRefusal(RefusalScope.EVENT, surprise.status.value, surprise.reason))
    listing = study.alignment.listing_resolution
    if listing.status is not ListingResolutionStatus.RESOLVED:
        out.append(ObservationRefusal(RefusalScope.MARKET, listing.status.value, listing.reason))
    alignment = study.alignment.session_alignment
    if alignment is not None and alignment.status is not AlignmentStatus.ALIGNED:
        out.append(
            ObservationRefusal(RefusalScope.MARKET, alignment.status.value, alignment.reason)
        )
    for forward in study.stock_returns.forward_returns:
        if forward.status not in (ForwardReturnStatus.AVAILABLE, ForwardReturnStatus.NO_ALIGNMENT):
            out.append(
                ObservationRefusal(
                    RefusalScope.MARKET, forward.status.value, forward.reason, forward.horizon
                )
            )
    if study.benchmark_id is not None:
        for bench in study.benchmark_returns:
            if bench.status not in (
                BenchmarkReturnStatus.AVAILABLE,
                BenchmarkReturnStatus.NO_STOCK_RETURN,
            ):
                out.append(
                    ObservationRefusal(
                        RefusalScope.BENCHMARK, bench.status.value, bench.reason, bench.horizon
                    )
                )
    link = observation.filing_link
    if link.status in (FilingLinkStatus.FILING_NOT_STORED, FilingLinkStatus.CIK_MISMATCH_REFUSED):
        out.append(ObservationRefusal(RefusalScope.FILING_LINK, link.status.value, link.reason))
    change = observation.accounting.change
    if change is not None:
        comparative = change.comparative
        if comparative.status is ComparativeEpsStatus.CURRENT_UNAVAILABLE:
            if comparative.current.status is not EpsStatus.AVAILABLE:
                out.append(
                    ObservationRefusal(
                        RefusalScope.ACCOUNTING,
                        comparative.current.status.value,
                        comparative.current.reason,
                    )
                )
        elif comparative.status is ComparativeEpsStatus.AVAILABLE:
            if change.percentage_unavailable_reason is not None:
                out.append(
                    ObservationRefusal(
                        RefusalScope.ACCOUNTING,
                        change.percentage_unavailable_reason.value,
                        "the EPS change is computed but its percentage is not defined",
                    )
                )
        else:
            out.append(
                ObservationRefusal(
                    RefusalScope.ACCOUNTING, comparative.status.value, comparative.reason
                )
            )
    pit = observation.point_in_time
    if pit.study is not None:
        out.extend(_pit_study_refusals(pit.study))
    if pit.eligibility is not PointInTimeEligibility.ELIGIBLE:
        out.append(
            ObservationRefusal(
                RefusalScope.POINT_IN_TIME,
                pit.eligibility.value,
                "no approved availability evidence supports a point-in-time claim for this event",
            )
        )
    return tuple(out)
