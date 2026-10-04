"""Public response schemas for ``GET /api/v1/research/observations/{event_id}``.

Pure transport types over the 10.8A :class:`~quantforge.observation.models.ResearchObservation`.
Hierarchy and enums mirror the domain exactly (no renaming, no reinterpretation, no arithmetic).
Every financial ``Decimal`` is a fixed-point JSON *string* (:data:`~quantforge.schemas.common.DecimalText`).

Accounting reuses the existing 10.6J :class:`~quantforge.schemas.filing_eps.FilingEpsResponse` whole,
so its descriptive/no-PIT constants and statuses are one contract.

Deliberately absent: filing ``accepted_at`` (the domain does not carry it), any ``known_at`` /
``available_at`` / ``disclosed_at`` concept, the domain's ``known_gaps`` roadmap list and the
unexposed internal pins (stock/benchmark source ids are not discoverable through any public
endpoint). The two sections are independent by contract: a REFUSED point-in-time section, an
unavailable accounting section or a refused filing link never removes descriptive data.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantforge.financials.eps import EpsKind, EpsPeriodClass
from quantforge.identity.listing_history import Evidence
from quantforge.models.company import CIK_PATTERN
from quantforge.models.enums import (
    AvailabilityBasis,
    EarningsTiming,
    PriceBasis,
    SecFormType,
)
from quantforge.models.filing import ACCESSION_PATTERN
from quantforge.observation.models import (
    AccountingState,
    DisclosureEvidenceLevel,
    FilingLinkStatus,
    PeriodVerification,
    PointInTimeState,
    RefusalScope,
    SectionState,
)
from quantforge.research.abnormal_returns import AbnormalReturnStatus
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturnStatus
from quantforge.research.calendar import AlignmentStatus
from quantforge.research.eps_surprise import EpsSurpriseStatus
from quantforge.research.listing_resolution import ListingResolutionStatus
from quantforge.research.returns import ForwardReturnStatus
from quantforge.schemas.common import DecimalText
from quantforge.schemas.filing_eps import FilingEpsResponse

__all__ = [
    "MAX_HORIZON",
    "MAX_HORIZONS",
    "AbnormalReturnOut",
    "AccountingOut",
    "AlignmentOut",
    "BenchmarkOut",
    "BenchmarkReturnOut",
    "DescriptiveOut",
    "DisclosureEvidenceOut",
    "EpsSurpriseOut",
    "EventOut",
    "FilingLinkOut",
    "ForwardReturnOut",
    "IdentityOut",
    "ListingResolutionOut",
    "ObservationParametersOut",
    "ObservationQuery",
    "PointInTimeOut",
    "ProvenanceOut",
    "RefusalOut",
    "ResearchObservationResponse",
    "SessionAlignmentOut",
    "StockReturnsOut",
    "StudyOut",
]

#: Request-size bounds only (they keep the price-window query bounded); not financial semantics.
MAX_HORIZONS: Final = 10
MAX_HORIZON: Final = 252

_HORIZONS_PATTERN = re.compile(r"^[1-9][0-9]*(,[1-9][0-9]*)*$")


class ObservationQuery(BaseModel):
    """The supported query parameters. ``extra="forbid"`` rejects any other parameter (422)."""

    model_config = {"extra": "forbid"}

    horizons: str | None = Field(
        None,
        max_length=64,
        description=(
            "Comma-separated positive session counts, e.g. 1,5,20 (at most 10, each at most 252, "
            "no duplicates). Absent: the research default 1,5,20. Echoed ascending in "
            "parameters.horizons."
        ),
    )
    benchmark_id: uuid.UUID | None = Field(
        None, description="Explicit benchmark. Absent: no benchmark (never inferred)."
    )
    security_id: uuid.UUID | None = Field(
        None, description="Disambiguates a multi-security issuer (the existing research knob)."
    )
    filing_accession: str | None = Field(
        None,
        pattern=ACCESSION_PATTERN,
        description=(
            "Caller-supplied dashed accession. Absent: NOT_LINKED; a filing is never inferred. "
            "Requires eps_period_class and eps_kind."
        ),
    )
    eps_period_class: EpsPeriodClass | None = Field(None, description="Only with filing_accession.")
    eps_kind: EpsKind | None = Field(None, description="Only with filing_accession.")

    @field_validator("horizons")
    @classmethod
    def _check_horizons(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _HORIZONS_PATTERN.fullmatch(value):
            message = "horizons must be comma-separated positive integers without spaces"
            raise ValueError(message)
        parts = [int(part) for part in value.split(",")]
        if len(set(parts)) != len(parts):
            message = "horizons must not contain duplicates"
            raise ValueError(message)
        if len(parts) > MAX_HORIZONS or max(parts) > MAX_HORIZON:
            message = f"at most {MAX_HORIZONS} horizons, each at most {MAX_HORIZON} sessions"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _check_filing_dependency(self) -> Self:
        selectors = (self.eps_period_class, self.eps_kind)
        if self.filing_accession is None and any(item is not None for item in selectors):
            message = "eps_period_class and eps_kind are only valid with filing_accession"
            raise ValueError(message)
        if self.filing_accession is not None and any(item is None for item in selectors):
            message = "filing_accession requires both eps_period_class and eps_kind"
            raise ValueError(message)
        return self

    def effective_horizons(self) -> tuple[int, ...] | None:
        """Ascending, deduplicated horizons, or ``None`` for the research default."""
        if self.horizons is None:
            return None
        return tuple(sorted(int(part) for part in self.horizons.split(",")))


class ObservationParametersOut(BaseModel):
    """Every effective input, defaults included."""

    horizons: list[int]
    price_basis: PriceBasis
    security_id: uuid.UUID | None
    benchmark_id: uuid.UUID | None
    filing_accession: str | None
    eps_period_class: EpsPeriodClass | None
    eps_kind: EpsKind | None


class ListingResolutionOut(BaseModel):
    status: ListingResolutionStatus
    listing_id: uuid.UUID | None
    security_id: uuid.UUID | None
    exchange_mic: str | None
    evidence: Evidence | None
    reason: str


class IdentityOut(BaseModel):
    company_id: uuid.UUID
    company_cik: str | None = Field(pattern=CIK_PATTERN)
    event_id: uuid.UUID
    listing_resolution: ListingResolutionOut
    benchmark_id: uuid.UUID | None
    filing_accession: str | None


class EventOut(BaseModel):
    """The selected vendor observation as stored. ``observed_at`` is vendor availability evidence
    only together with ``observed_at_basis``; both are null when the source gave none."""

    event_id: uuid.UUID
    fingerprint: str
    fiscal_year: int
    fiscal_quarter: int | None
    event_date: date
    event_timestamp: datetime | None
    timing: EarningsTiming
    reported_eps: DecimalText | None
    estimated_eps: DecimalText | None
    observed_at: datetime | None
    observed_at_basis: AvailabilityBasis | None


class FilingLinkOut(BaseModel):
    status: FilingLinkStatus
    period_verification: PeriodVerification
    accession_number: str | None
    company_cik: str | None
    form: SecFormType | None
    reason: str


class DisclosureEvidenceOut(BaseModel):
    """Stored-metadata category only. There is deliberately no time field, and no level
    implies availability or point-in-time eligibility."""

    level: DisclosureEvidenceLevel
    accession_number: str | None
    form: SecFormType | None
    items: str | None


class AccountingOut(BaseModel):
    state: AccountingState
    eps: FilingEpsResponse | None = Field(
        description="The existing filing EPS contract, present only when state is EVALUATED."
    )


class EpsSurpriseOut(BaseModel):
    status: EpsSurpriseStatus
    reported_eps: DecimalText | None
    estimated_eps: DecimalText | None
    absolute_surprise: DecimalText | None
    surprise_ratio: DecimalText | None
    reason: str


class SessionAlignmentOut(BaseModel):
    status: AlignmentStatus
    event_session: date | None
    first_post_event_session: date | None
    reason: str


class AlignmentOut(BaseModel):
    event_date: date
    timing: EarningsTiming
    session_alignment: SessionAlignmentOut | None


class ForwardReturnOut(BaseModel):
    horizon: int
    status: ForwardReturnStatus
    anchor_session: date | None
    anchor_close: DecimalText | None
    target_session: date | None
    target_close: DecimalText | None
    return_value: DecimalText | None
    reason: str


class StockReturnsOut(BaseModel):
    listing_id: uuid.UUID | None
    price_source_id: uuid.UUID | None
    anchor_session: date | None
    forward_returns: list[ForwardReturnOut]


class BenchmarkReturnOut(BaseModel):
    horizon: int
    status: BenchmarkReturnStatus
    anchor_session: date | None
    anchor_close: DecimalText | None
    target_session: date | None
    target_close: DecimalText | None
    benchmark_return: DecimalText | None
    reason: str


class BenchmarkOut(BaseModel):
    benchmark_id: uuid.UUID | None
    returns: list[BenchmarkReturnOut]


class AbnormalReturnOut(BaseModel):
    horizon: int
    status: AbnormalReturnStatus
    abnormal_return: DecimalText | None
    reason: str


class StudyOut(BaseModel):
    """One event-study result, whole, in one research mode."""

    research_mode: ResearchMode
    point_in_time_eligibility: PointInTimeEligibility
    eps_surprise: EpsSurpriseOut
    alignment: AlignmentOut
    stock_returns: StockReturnsOut
    benchmark: BenchmarkOut
    abnormal_returns: list[AbnormalReturnOut]
    price_basis_note: str


class DescriptiveOut(BaseModel):
    state: SectionState
    study: StudyOut


class PointInTimeOut(BaseModel):
    state: PointInTimeState
    eligibility: PointInTimeEligibility
    study: StudyOut | None = Field(
        description="Present only when availability evidence is eligible; null when REFUSED."
    )


class ArtifactDigestOut(BaseModel):
    role: Literal["current", "comparative"]
    sha256: str


class ProvenanceOut(BaseModel):
    """Existing references only. Benchmark ingestion run, methodology/calendar versions and an
    input manifest do not exist yet and are not represented."""

    event_fingerprint: str
    event_source_id: uuid.UUID
    event_ingestion_run_id: uuid.UUID | None
    stock_price_source_id: uuid.UUID | None
    benchmark_id: uuid.UUID | None
    filing_source_id: uuid.UUID | None
    filing_ingestion_run_id: uuid.UUID | None
    accounting_artifacts: list[ArtifactDigestOut]


class RefusalOut(BaseModel):
    """One unavailable part: the scope and the originating layer's own status name as ``code``."""

    scope: RefusalScope
    code: str
    reason: str
    horizon: int | None


class ResearchObservationResponse(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    identity: IdentityOut
    parameters: ObservationParametersOut
    event: EventOut
    filing_link: FilingLinkOut
    disclosure_evidence: DisclosureEvidenceOut
    accounting: AccountingOut
    descriptive: DescriptiveOut
    point_in_time: PointInTimeOut
    provenance: ProvenanceOut
    refusals: list[RefusalOut]
