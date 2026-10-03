"""The availability-evidence contract: what ``observed_at`` proves, and who may rely on it.

``observed_at`` is meaningless without ``observed_at_basis``; only an *approved*
basis ever supports a point-in-time claim; and nothing weaker (a date, a record
time, an ingestion time) can pose as one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from quantforge.ingestion.canonical import CanonicalEarningsEvent
from quantforge.ingestion.earnings import earnings_fingerprint
from quantforge.models.earnings import EarningsEvent, earnings_event_fingerprint
from quantforge.models.enums import AvailabilityBasis, EarningsTiming
from quantforge.providers.finnhub.dto import FinnhubEarningsCalendarEntry
from quantforge.providers.finnhub.mapper import to_canonical_earnings_event
from quantforge.research.availability import (
    APPROVED_AVAILABILITY_BASES,
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
    is_point_in_time_availability_eligible,
    select_known_as_of,
)
from quantforge.schemas.earnings import EarningsEventCreate

_T = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
_SOURCE = AvailabilityBasis.SOURCE_PUBLICATION
_SEC = AvailabilityBasis.SEC_FILING_ACCEPTANCE


# ── the central policy ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("observed_at", "basis", "eligible"),
    [
        (None, None, False),  # no evidence
        (None, _SOURCE, False),  # a basis with no timestamp
        (_T, None, False),  # a timestamp that does not say what it proves
        (_T, _SEC, False),  # SEC acceptance is defined but NOT approved yet
        (_T, _SOURCE, True),  # the provider's own publication instant
    ],
)
def test_only_a_timestamp_with_an_approved_basis_is_eligible(
    observed_at: datetime | None, basis: AvailabilityBasis | None, eligible: bool
) -> None:
    assert is_point_in_time_availability_eligible(observed_at, basis) is eligible


def test_the_approved_set_is_exactly_source_publication() -> None:
    assert frozenset({AvailabilityBasis.SOURCE_PUBLICATION}) == APPROVED_AVAILABILITY_BASES
    assert _SEC not in APPROVED_AVAILABILITY_BASES


def test_every_basis_is_either_approved_or_explicitly_not() -> None:
    """A new basis can never silently become tradable: it is in the set only by edit."""
    assert {b.value for b in AvailabilityBasis} == {"SOURCE_PUBLICATION", "SEC_FILING_ACCEPTANCE"}


def test_classification_distinguishes_missing_evidence_from_unapproved_evidence() -> None:
    pit = ResearchMode.POINT_IN_TIME
    assert (
        classify_point_in_time_eligibility(pit, observed_at=None, observed_at_basis=None)
        is PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    )
    assert (
        classify_point_in_time_eligibility(pit, observed_at=_T, observed_at_basis=_SEC)
        is PointInTimeEligibility.INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS
    )
    assert (
        classify_point_in_time_eligibility(pit, observed_at=_T, observed_at_basis=None)
        is PointInTimeEligibility.INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS
    )
    assert (
        classify_point_in_time_eligibility(pit, observed_at=_T, observed_at_basis=_SOURCE)
        is PointInTimeEligibility.ELIGIBLE
    )
    assert (
        classify_point_in_time_eligibility(
            ResearchMode.DESCRIPTIVE, observed_at=_T, observed_at_basis=_SEC
        )
        is PointInTimeEligibility.NOT_APPLICABLE
    )


def _event(**overrides: object) -> EarningsEvent:
    fields: dict[str, object] = {
        "company_id": uuid.uuid4(),
        "fiscal_year": 2026,
        "fiscal_quarter": 2,
        "event_date": date(2026, 6, 15),
        "timing": EarningsTiming.AFTER_MARKET,
        "source_id": uuid.uuid4(),
        "observed_at": None,
        "observed_at_basis": None,
    }
    fields.update(overrides)
    return EarningsEvent(**fields)


def test_revision_selection_ignores_evidence_that_is_not_approved() -> None:
    sec_only = _event(observed_at=_T, observed_at_basis=_SEC)
    approved = _event(observed_at=_T - timedelta(hours=1), observed_at_basis=_SOURCE)

    assert select_known_as_of([sec_only], as_of=_T + timedelta(days=1)) is None
    assert select_known_as_of([sec_only, approved], as_of=_T + timedelta(days=1)) is approved


# ── the canonical event and the pydantic boundary ───────────────────────────


def _canonical(**overrides: object) -> CanonicalEarningsEvent:
    fields: dict[str, object] = {
        "ticker": "AAPL",
        "fiscal_year": 2026,
        "fiscal_quarter": 2,
        "event_date": date(2026, 6, 15),
        "timing": EarningsTiming.AFTER_MARKET,
    }
    fields.update(overrides)
    return CanonicalEarningsEvent(**fields)  # type: ignore[arg-type]


def test_null_null_and_timestamp_with_basis_are_valid() -> None:
    assert _canonical().observed_at is None
    assert _canonical().observed_at_basis is None
    event = _canonical(observed_at=_T, observed_at_basis=_SOURCE)
    assert event.observed_at == _T and event.observed_at_basis is _SOURCE


def test_a_timestamp_without_a_basis_is_refused() -> None:
    with pytest.raises(ValueError, match="set together"):
        _canonical(observed_at=_T)


def test_a_basis_without_a_timestamp_is_refused() -> None:
    with pytest.raises(ValueError, match="set together"):
        _canonical(observed_at_basis=_SOURCE)


def test_an_unknown_basis_is_refused() -> None:
    with pytest.raises(ValueError, match="AvailabilityBasis"):
        _canonical(observed_at=_T, observed_at_basis="FIRST_PUBLIC")


def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _canonical(observed_at=datetime(2026, 6, 15, 20, 5), observed_at_basis=_SOURCE)  # noqa: DTZ001


def test_a_date_is_never_accepted_as_an_availability_instant() -> None:
    """A date-only value is not an instant and is never promoted to midnight."""
    with pytest.raises(ValueError, match="datetime instant"):
        _canonical(observed_at=date(2026, 6, 15), observed_at_basis=_SOURCE)


def test_a_timezone_aware_non_utc_timestamp_is_accepted_and_denotes_the_same_instant() -> None:
    eastern = timezone(timedelta(hours=-4))
    event = _canonical(
        observed_at=datetime(2026, 6, 15, 8, 0, tzinfo=eastern), observed_at_basis=_SOURCE
    )

    assert event.observed_at == datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _create(**overrides: object) -> EarningsEventCreate:
    fields: dict[str, object] = {
        "company_id": uuid.uuid4(),
        "fiscal_year": 2026,
        "event_date": date(2026, 6, 15),
        "source_id": uuid.uuid4(),
    }
    fields.update(overrides)
    return EarningsEventCreate(**fields)


def test_the_pydantic_boundary_enforces_the_same_pairing() -> None:
    assert _create().observed_at_basis is None
    assert _create(observed_at=_T, observed_at_basis=_SEC).observed_at_basis is _SEC
    with pytest.raises(ValidationError, match="together"):
        _create(observed_at=_T)
    with pytest.raises(ValidationError, match="together"):
        _create(observed_at_basis=_SOURCE)
    with pytest.raises(ValidationError):
        _create(observed_at=_T, observed_at_basis="NOPE")


# ── fingerprints stay stable for every row written before the basis existed ──

_FACTS: dict[str, object] = {
    "fiscal_year": 2026,
    "fiscal_quarter": 2,
    "event_date": date(2026, 6, 15),
    "event_timestamp": None,
    "timing": EarningsTiming.AFTER_MARKET,
    "reported_eps": Decimal("1.5"),
    "estimated_eps": Decimal("1.4"),
    "reported_revenue": None,
    "estimated_revenue": None,
    "currency": None,
    "observed_at": None,
}


def test_a_row_with_no_basis_keeps_exactly_its_pre_existing_fingerprint() -> None:
    with_default = earnings_event_fingerprint(**_FACTS, observed_at_basis=None)  # type: ignore[arg-type]
    # Pinned golden value, produced by the committed pre-basis implementation: if this
    # changes, every stored fingerprint stops matching and re-fetches would duplicate rows.
    assert with_default == "d0665581204363ebf3a9bd81de78a2037d31a892009f2ebdf7ff65c31b34c4cd"


def test_the_basis_is_part_of_the_fact_when_present() -> None:
    facts = {**_FACTS, "observed_at": _T}
    source = earnings_event_fingerprint(**facts, observed_at_basis=_SOURCE)  # type: ignore[arg-type]
    sec = earnings_event_fingerprint(**facts, observed_at_basis=_SEC)  # type: ignore[arg-type]

    assert source != sec, "the same instant with a different meaning is a different observation"


def test_the_ingestion_fingerprint_agrees_with_the_model_fingerprint() -> None:
    event = _canonical(
        reported_eps=Decimal("1.5"), observed_at=_T, observed_at_basis=_SOURCE, currency=None
    )
    model = earnings_event_fingerprint(
        fiscal_year=2026,
        fiscal_quarter=2,
        event_date=date(2026, 6, 15),
        event_timestamp=None,
        timing=EarningsTiming.AFTER_MARKET,
        reported_eps=Decimal("1.5"),
        estimated_eps=None,
        reported_revenue=None,
        estimated_revenue=None,
        currency=None,
        observed_at=_T,
        observed_at_basis=_SOURCE,
    )

    assert earnings_fingerprint(event) == model


# ── Finnhub stays NULL/NULL ─────────────────────────────────────────────────


def test_finnhub_events_carry_no_availability_evidence_at_all() -> None:
    entry = FinnhubEarningsCalendarEntry.model_validate(
        {
            "symbol": "AAPL",
            "date": "2026-06-15",
            "year": 2026,
            "quarter": 2,
            "hour": "amc",
            "epsActual": 1.5,
            "epsEstimate": 1.4,
        }
    )

    event = to_canonical_earnings_event(entry, identifier="AAPL")

    assert event.observed_at is None
    assert event.observed_at_basis is None
