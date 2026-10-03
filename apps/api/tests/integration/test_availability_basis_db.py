"""Availability basis against real PostgreSQL, and the proof that nothing weaker than
approved evidence can make a signal executable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.backtest import (
    ExecutionEligibility,
    ExecutionEligibilityStatus,
    assess_execution_eligibility,
)
from quantforge.ingestion.canonical import CanonicalEarningsEvent
from quantforge.ingestion.earnings import EarningsIngestor
from quantforge.market import ExchangeCalendarsSessionCalendar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    AvailabilityBasis,
    EarningsTiming,
    PriceBasis,
    ProviderType,
    SecurityType,
)
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.signals.eps_surprise import EpsSurpriseSignal, generate_eps_surprise_signal

pytestmark = pytest.mark.integration

#: Friday 2024-03-15; XNYS opens 13:30Z.
_FRIDAY = date(2024, 3, 15)
_BEFORE_OPEN = datetime(2024, 3, 15, 11, 0, tzinfo=UTC)


@pytest.fixture
def source(db_session: Session) -> DataSource:
    row = DataSource(name="basis-test-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Basis Test Inc.", display_name="Basis Test")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def listing(company: Company, db_session: Session) -> Listing:
    exchange = Exchange(mic="XNYS", name="NYSE test", timezone="America/New_York")
    security = Security(
        company_id=company.id, security_type=SecurityType.EQUITY, name="Shares", currency="USD"
    )
    db_session.add_all([exchange, security])
    db_session.commit()
    row = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker="BSIS",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(row)
    db_session.commit()
    for day in (date(2024, 3, 14), date(2024, 3, 15), date(2024, 3, 18)):
        db_session.add(
            DailyPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                listing_id=row.id,
                trading_date=day,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                source_id=db_session.query(DataSource).one().id,
            )
        )
    db_session.commit()
    return row


def _event(
    company: Company, source: DataSource, session: Session, **overrides: object
) -> EarningsEvent:
    fields: dict[str, object] = {
        "company_id": company.id,
        "fiscal_year": 2024,
        "fiscal_quarter": 1,
        "event_date": _FRIDAY,
        "timing": EarningsTiming.BEFORE_MARKET,
        "reported_eps": Decimal("1.1"),
        "estimated_eps": Decimal("1.0"),
        "source_id": source.id,
    }
    fields.update(overrides)
    row = EarningsEvent(**fields)
    session.add(row)
    session.flush()
    return row


# ── the database enforces the pairing ───────────────────────────────────────


def test_null_null_is_valid(company: Company, source: DataSource, db_session: Session) -> None:
    row = _event(company, source, db_session)

    assert row.observed_at is None and row.observed_at_basis is None


def test_timestamp_with_a_basis_round_trips(
    company: Company, source: DataSource, db_session: Session
) -> None:
    _event(
        company,
        source,
        db_session,
        observed_at=_BEFORE_OPEN,
        observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION,
    )
    db_session.commit()

    stored = db_session.query(EarningsEvent).one()
    assert stored.observed_at == _BEFORE_OPEN
    assert stored.observed_at_basis is AvailabilityBasis.SOURCE_PUBLICATION


def test_a_timestamp_without_a_basis_is_rejected_by_the_database(
    company: Company, source: DataSource, db_session: Session
) -> None:
    with pytest.raises(sa.exc.IntegrityError, match="observed_at_basis_pairing"):
        _event(company, source, db_session, observed_at=_BEFORE_OPEN)
    db_session.rollback()


def test_a_basis_without_a_timestamp_is_rejected_by_the_database(
    company: Company, source: DataSource, db_session: Session
) -> None:
    with pytest.raises(sa.exc.IntegrityError, match="observed_at_basis_pairing"):
        _event(company, source, db_session, observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION)
    db_session.rollback()


def test_an_unknown_basis_is_rejected_by_the_database(
    company: Company, source: DataSource, db_session: Session
) -> None:
    row = _event(company, source, db_session)
    with pytest.raises(sa.exc.IntegrityError, match="observed_at_basis_enum_values"):
        db_session.execute(
            sa.text(
                "UPDATE earnings_event SET observed_at = now(), observed_at_basis = 'FIRST_PUBLIC' "
                "WHERE id = :id"
            ),
            {"id": row.id},
        )
    db_session.rollback()


def test_ingested_at_never_becomes_observed_at_through_ingestion(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    now = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    EarningsIngestor(db_session, now=now).ingest(
        [
            CanonicalEarningsEvent(
                ticker="BSIS",
                fiscal_year=2024,
                fiscal_quarter=1,
                event_date=_FRIDAY,
                timing=EarningsTiming.BEFORE_MARKET,
            )
        ],
        source_id=source.id,
    )
    db_session.commit()

    row = db_session.query(EarningsEvent).one()
    assert row.ingested_at == now
    assert row.observed_at is None and row.observed_at_basis is None


# ── nothing weaker than approved evidence becomes executable ────────────────


def _assess(
    db_session: Session, event: EarningsEvent
) -> tuple[EpsSurpriseSignal, ExecutionEligibility]:
    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)
    return signal, assess_execution_eligibility(signal, ExchangeCalendarsSessionCalendar())


def test_a_finnhub_shaped_event_is_never_executable(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    event = _event(company, source, db_session)  # observed_at NULL, basis NULL: what Finnhub writes

    signal, result = _assess(db_session, event)

    assert (
        signal.point_in_time_eligibility
        is PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    )
    assert result.status is ExecutionEligibilityStatus.NOT_POINT_IN_TIME_ELIGIBLE
    assert result.candidate_execution_session is None


def test_sec_filing_acceptance_evidence_is_not_executable_until_approved(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """Even a timestamp comfortably before the open does not qualify on an unapproved basis."""
    event = _event(
        company,
        source,
        db_session,
        observed_at=_BEFORE_OPEN,
        observed_at_basis=AvailabilityBasis.SEC_FILING_ACCEPTANCE,
    )

    signal, result = _assess(db_session, event)

    assert (
        signal.point_in_time_eligibility
        is PointInTimeEligibility.INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS
    )
    assert signal.observed_at_basis is AvailabilityBasis.SEC_FILING_ACCEPTANCE
    assert result.status is ExecutionEligibilityStatus.NOT_POINT_IN_TIME_ELIGIBLE


def test_approved_source_publication_before_the_open_remains_executable(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """The approved path is unchanged: only the evidence kind decides."""
    event = _event(
        company,
        source,
        db_session,
        observed_at=_BEFORE_OPEN,
        observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION,
    )

    signal, result = _assess(db_session, event)

    assert signal.point_in_time_eligibility is PointInTimeEligibility.ELIGIBLE
    assert result.status is ExecutionEligibilityStatus.CANDIDATE_EXECUTION_ELIGIBLE
    assert result.observed_at_basis is AvailabilityBasis.SOURCE_PUBLICATION


def test_ingested_at_can_never_change_point_in_time_eligibility(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """Architectural tripwire: the ingestion clock is not availability evidence, at any value."""
    early = datetime(2024, 3, 15, 1, 0, tzinfo=UTC)  # long before the open
    late = datetime(2030, 1, 1, tzinfo=UTC)
    results = []
    for index, ingested in enumerate((early, late)):
        event = _event(company, source, db_session, fiscal_quarter=index + 1, ingested_at=ingested)
        signal, result = _assess(db_session, event)
        results.append((signal.point_in_time_eligibility, result.status, event.ingested_at))

    assert all(
        eligibility is PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
        and status is ExecutionEligibilityStatus.NOT_POINT_IN_TIME_ELIGIBLE
        for eligibility, status, _ in results
    )
    assert results[0][2] != results[1][2], "the two rows really differ only in ingested_at"
