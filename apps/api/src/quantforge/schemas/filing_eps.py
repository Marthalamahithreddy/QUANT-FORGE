"""Public response schemas for ``GET /api/v1/filings/{accession}/eps``.

Pure transport types over the existing financial-domain observations
(:mod:`quantforge.financials`). They carry no financial rule: statuses, reasons, transitions and
values are exactly what the domain produced. Every financial ``Decimal`` is serialised as a
fixed-point JSON *string* (:data:`~quantforge.schemas.common.DecimalText`), never a JSON number.

Finite, canonical values: the endpoint relies on the validated domain contract. An AVAILABLE EPS
observation rejects a non-finite value, a missing value is ``null`` (never ``NaN``), the domain
normalises a zero ``eps_change`` to ``+0``, and stored facts come from PostgreSQL ``NUMERIC``, which
has no negative zero, so no ``NaN``, ``Infinity`` or ``-0`` can reach the wire.

Deliberately absent: any availability or acceptance time, ingestion times, run ids, raw paths and any
trading semantics. The response states, machine-readably, that it is descriptive only and makes no
point-in-time claim.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from quantforge.financials.comparative_eps import ComparativeEpsStatus
from quantforge.financials.eps import EpsKind, EpsPeriodClass, EpsStatus
from quantforge.financials.eps_change import (
    EpsChangeStatus,
    EpsTransition,
    PercentageUnavailableReason,
)
from quantforge.models.company import CIK_PATTERN
from quantforge.models.enums import SecFormType
from quantforge.schemas.common import DecimalText

__all__ = [
    "BOUNDARY_NOTE",
    "COMPARATIVE_MEANING",
    "ComparativeEpsSection",
    "CurrentEpsSection",
    "EpsChangeSection",
    "FactEvidence",
    "FilingEpsQuery",
    "FilingEpsResponse",
]

#: The machine-readable boundary, repeated in prose so a consumer cannot miss it.
BOUNDARY_NOTE: Final = (
    "Descriptive accounting data from SEC FSDS. No point-in-time availability claim: nothing here "
    "says when a figure was known or that it was tradable."
)
COMPARATIVE_MEANING: Final = "comparative EPS as presented in this filing"


class FilingEpsQuery(BaseModel):
    """``period_class`` and ``eps_kind`` are required (nothing is defaulted or inferred from the form);
    ``cik`` is optional identity qualification."""

    model_config = {"extra": "forbid"}

    period_class: EpsPeriodClass = Field(description="QUARTER (qtrs = 1) or ANNUAL (qtrs = 4).")
    cik: str | None = Field(
        None,
        pattern=CIK_PATTERN,
        description=(
            "Optional ten-digit zero-padded registrant CIK that qualifies WHICH stored filing "
            "(company, accession) is meant, for an accession stored under several registrants. "
            "Identity only: it never changes how EPS is selected. Absent: the accession alone "
            "resolves, and a joint accession is reported AMBIGUOUS. Present but matching no "
            "stored filing: NOT_INGESTED for that CIK (never another registrant's filing)."
        ),
    )
    eps_kind: EpsKind = Field(
        description=(
            "DILUTED (EarningsPerShareDiluted) or BASIC_AND_DILUTED "
            "(EarningsPerShareBasicAndDiluted). Never a fallback between them."
        )
    )


class FactEvidence(BaseModel):
    """One stored fact, as the domain selected it. No ingestion/availability fields."""

    fact_id: uuid.UUID
    tag: str
    taxonomy_version: str
    unit: str
    period_end: date = Field(description="The source's month-end-rounded period end.")
    qtrs: int = Field(description="Quarters spanned (FSDS qtrs): 1 = a quarter, 4 = a year.")
    value: DecimalText | None = Field(
        description="Exact fixed-point string; null if the source stated none."
    )
    segments: str = Field(description="Dimension text verbatim; empty when the source states none.")
    coreg: str = Field(
        description="Co-registrant text verbatim; empty when the source states none."
    )


class CurrentEpsSection(BaseModel):
    status: EpsStatus
    reason: str
    fact: FactEvidence | None = Field(description="Present only when a fact was selected.")


class ComparativeEpsSection(BaseModel):
    # Constants have defaults but are always emitted: required in the serialised-response schema.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    status: ComparativeEpsStatus
    reason: str
    meaning: Literal["comparative EPS as presented in this filing"] = Field(
        default=COMPARATIVE_MEANING,
        description="Not the originally reported prior-year EPS; it may be restated.",
    )
    fact: FactEvidence | None = Field(
        description="Present only when the domain identified an authoritative comparative fact."
    )


class EpsChangeSection(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    status: EpsChangeStatus
    eps_change: DecimalText | None = Field(
        description="current - comparative: signed, exact, fixed-point string."
    )
    transition: EpsTransition | None
    percentage_change: DecimalText | None = Field(
        description="(current - comparative) / comparative as a RATIO string (0.5 = +50%)."
    )
    percentage_semantics: Literal["ratio"] = "ratio"
    percentage_unavailable_reason: PercentageUnavailableReason | None = Field(
        description="Why percentage_change is null although eps_change exists."
    )


class FilingEpsResponse(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    accession: str
    company_cik: str | None = Field(
        description=(
            "The registrant's CIK when a filing resolved; when a CIK was requested and no stored "
            "filing matched, the requested CIK is echoed with a NOT_INGESTED status (a request "
            "echo, not a resolved identity). Null for an unqualified refusal."
        )
    )
    form: SecFormType | None
    period_class: EpsPeriodClass
    eps_kind: EpsKind
    descriptive_only: Literal[True] = True
    no_point_in_time_claim: Literal[True] = True
    boundary_note: str = BOUNDARY_NOTE
    current: CurrentEpsSection
    comparative: ComparativeEpsSection
    change: EpsChangeSection
