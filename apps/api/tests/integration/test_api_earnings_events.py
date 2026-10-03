"""Read-only earnings-event API against real PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    AvailabilityBasis,
    EarningsTiming,
    ProviderType,
    SecurityType,
)
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security

pytestmark = pytest.mark.integration

BASE = "/api/v1/earnings-events"
_OBSERVED = datetime(2024, 3, 15, 11, 0, tzinfo=UTC)


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def source(db_session: Session) -> DataSource:
    row = DataSource(name="api-test-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Api Test Inc.", display_name="Api Test", cik="0000320193")
    db_session.add(row)
    db_session.flush()
    exchange = Exchange(mic="XNAS", name="Nasdaq test", timezone="America/New_York")
    security = Security(
        company_id=row.id, security_type=SecurityType.EQUITY, name="Shares", currency="USD"
    )
    db_session.add_all([exchange, security])
    db_session.flush()
    db_session.add_all(
        [
            Listing(
                security_id=security.id,
                exchange_id=exchange.id,
                ticker="OLDT",
                valid_from=date(2010, 1, 1),
                valid_to=date(2024, 1, 1),
                is_primary=False,
            ),
            Listing(
                security_id=security.id,
                exchange_id=exchange.id,
                ticker="APIT",
                valid_from=date(2024, 1, 1),
                is_primary=True,
            ),
        ]
    )
    db_session.flush()
    return row


def _event(
    company: Company, source: DataSource, session: Session, **overrides: object
) -> EarningsEvent:
    fields: dict[str, object] = {
        "company_id": company.id,
        "fiscal_year": 2024,
        "fiscal_quarter": 1,
        "event_date": date(2024, 3, 15),
        "timing": EarningsTiming.BEFORE_MARKET,
        "reported_eps": Decimal("1.1000"),
        "estimated_eps": Decimal("1.0000"),
        "source_id": source.id,
    }
    fields.update(overrides)
    row = EarningsEvent(**fields)
    session.add(row)
    session.flush()
    return row


def test_empty_database_returns_an_empty_page(client: TestClient) -> None:
    body = client.get(BASE).json()
    assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_event_without_availability_is_ineligible_with_a_reason(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(company, source, db_session)

    item = client.get(BASE).json()["items"][0]

    assert item["availability"]["observed_at"] is None
    assert item["availability"]["observed_at_basis"] is None
    assert item["availability"]["pit_eligibility"] == "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"
    assert "did not supply" in item["availability"]["pit_reason"]


def test_source_publication_is_eligible(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(
        company,
        source,
        db_session,
        observed_at=_OBSERVED,
        observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION,
    )

    availability = client.get(BASE).json()["items"][0]["availability"]

    assert availability["pit_eligibility"] == "ELIGIBLE"
    assert availability["observed_at_basis"] == "SOURCE_PUBLICATION"


def test_sec_filing_acceptance_is_not_eligible_even_with_a_timestamp(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(
        company,
        source,
        db_session,
        observed_at=_OBSERVED,
        observed_at_basis=AvailabilityBasis.SEC_FILING_ACCEPTANCE,
    )

    availability = client.get(BASE).json()["items"][0]["availability"]

    assert availability["observed_at"] is not None
    assert availability["observed_at_basis"] == "SEC_FILING_ACCEPTANCE"
    assert availability["pit_eligibility"] == "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS"


def test_decimals_are_exact_strings_not_numbers(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("0.3000"),
        estimated_eps=Decimal("0.1000"),
    )

    item = client.get(BASE).json()["items"][0]

    assert item["reported_eps"] == "0.3000"
    assert item["estimated_eps"] == "0.1000"
    assert item["eps_surprise"]["status"] == "AVAILABLE"
    assert item["eps_surprise"]["absolute"] == "0.2000"
    assert item["eps_surprise"]["ratio"] == "2"


def test_missing_eps_gives_an_explicit_surprise_status(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(company, source, db_session, estimated_eps=None)

    item = client.get(BASE).json()["items"][0]

    assert item["estimated_eps"] is None
    assert item["eps_surprise"]["status"] == "MISSING_ESTIMATED_EPS"
    assert item["eps_surprise"]["absolute"] is None


def test_listings_flag_validity_on_the_event_date(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(company, source, db_session, event_date=date(2023, 6, 1))

    listings = {x["ticker"]: x for x in client.get(BASE).json()["items"][0]["listings"]}

    assert listings["OLDT"]["valid_on_event_date"] is True
    assert listings["APIT"]["valid_on_event_date"] is False
    assert listings["APIT"]["mic"] == "XNAS"


def test_no_internal_fields_leak(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(company, source, db_session)

    text = client.get(BASE).text

    for forbidden in ("fingerprint", "ingestion_run_id", "raw", "path"):
        assert forbidden not in text


def test_ordering_pagination_and_total(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    for month in (1, 2, 3):
        _event(company, source, db_session, fiscal_quarter=month, event_date=date(2024, month, 10))

    page = client.get(BASE, params={"limit": 2, "offset": 1}).json()

    assert page["total"] == 3
    assert [i["event_date"] for i in page["items"]] == ["2024-02-10", "2024-01-10"]


def test_ticker_matches_only_a_listing_valid_on_the_event_date(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    # OLDT valid [2010-01-01, 2024-01-01); APIT valid [2024-01-01, open).
    _event(company, source, db_session, event_date=date(2023, 6, 1), fiscal_quarter=1)
    _event(company, source, db_session, event_date=date(2024, 5, 10), fiscal_quarter=2)

    old = client.get(BASE, params={"ticker": "OLDT"}).json()
    new = client.get(BASE, params={"ticker": "apit"}).json()
    nope = client.get(BASE, params={"ticker": "NOPE"}).json()

    assert [i["event_date"] for i in old["items"]] == ["2023-06-01"]
    assert [i["event_date"] for i in new["items"]] == ["2024-05-10"]
    assert nope["total"] == 0


def test_ticker_is_exact_not_partial(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(company, source, db_session, event_date=date(2024, 5, 10))

    assert client.get(BASE, params={"ticker": "API"}).json()["total"] == 0
    assert client.get(BASE, params={"ticker": "APIT"}).json()["total"] == 1


def test_listing_validity_is_half_open_at_both_boundaries(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    # OLDT ends (exclusive) on 2024-01-01, which is exactly when APIT starts.
    _event(company, source, db_session, event_date=date(2024, 1, 1))

    listings = {x["ticker"]: x for x in client.get(BASE).json()["items"][0]["listings"]}

    assert listings["OLDT"]["valid_on_event_date"] is False
    assert listings["APIT"]["valid_on_event_date"] is True
    assert client.get(BASE, params={"ticker": "OLDT"}).json()["total"] == 0
    assert client.get(BASE, params={"ticker": "APIT"}).json()["total"] == 1


def test_date_bounds_are_both_inclusive(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    for q, day in ((1, 10), (2, 20), (3, 30)):
        _event(company, source, db_session, fiscal_quarter=q, event_date=date(2024, 1, day))

    page = client.get(BASE, params={"date_from": "2024-01-10", "date_to": "2024-01-20"}).json()

    assert [i["event_date"] for i in page["items"]] == ["2024-01-20", "2024-01-10"]


def test_equal_dates_paginate_without_duplicates_or_gaps(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    ids = {
        str(_event(company, source, db_session, fiscal_quarter=None, fiscal_year=2000 + n).id)
        for n in range(7)
    }

    seen: list[str] = []
    for offset in range(0, 7, 3):
        page = client.get(BASE, params={"limit": 3, "offset": offset}).json()
        seen.extend(i["id"] for i in page["items"])

    assert len(seen) == 7 and set(seen) == ids


def test_revisions_of_one_period_are_separate_observations(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(company, source, db_session, reported_eps=Decimal("1.0000"))
    _event(company, source, db_session, reported_eps=Decimal("1.2000"))

    page = client.get(BASE).json()

    assert page["total"] == 2
    assert {i["reported_eps"] for i in page["items"]} == {"1.0000", "1.2000"}


@pytest.mark.parametrize(
    ("reported", "estimated", "status", "absolute", "ratio"),
    [
        ("1.1000", "1.0000", "AVAILABLE", "0.1000", "0.1"),
        ("0.9000", "1.0000", "AVAILABLE", "-0.1000", "-0.1"),
        ("1.0000", "1.0000", "AVAILABLE", "0.0000", "0"),
        ("0.5000", "0.0000", "ZERO_ESTIMATE", "0.5000", None),
        ("0.5000", None, "MISSING_ESTIMATED_EPS", None, None),
        (None, "0.5000", "MISSING_REPORTED_EPS", None, None),
    ],
)
def test_eps_surprise_cases_never_turn_unavailable_into_zero(
    client: TestClient,
    company: Company,
    source: DataSource,
    db_session: Session,
    reported: str | None,
    estimated: str | None,
    status: str,
    absolute: str | None,
    ratio: str | None,
) -> None:
    _event(
        company,
        source,
        db_session,
        reported_eps=None if reported is None else Decimal(reported),
        estimated_eps=None if estimated is None else Decimal(estimated),
    )

    surprise = client.get(BASE).json()["items"][0]["eps_surprise"]

    assert surprise["status"] == status
    assert surprise["absolute"] == absolute
    assert surprise["ratio"] == ratio


def test_decimals_never_use_exponent_notation(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("0.0001"),
        estimated_eps=Decimal("1000.0000"),
        reported_revenue=Decimal("12345678901234567890.1234"),
        estimated_revenue=Decimal("-0.0001"),
    )

    item = client.get(BASE).json()["items"][0]

    assert item["reported_revenue"] == "12345678901234567890.1234"
    assert item["estimated_revenue"] == "-0.0001"
    assert "E" not in item["eps_surprise"]["ratio"].upper()
    assert item["eps_surprise"]["ratio"] == "-0.9999999"


def test_ingested_at_never_changes_eligibility(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    _event(
        company, source, db_session, fiscal_quarter=1, ingested_at=datetime(2000, 1, 1, tzinfo=UTC)
    )
    _event(
        company, source, db_session, fiscal_quarter=2, ingested_at=datetime(2030, 1, 1, tzinfo=UTC)
    )

    states = {i["availability"]["pit_eligibility"] for i in client.get(BASE).json()["items"]}

    assert states == {"INELIGIBLE_NO_AVAILABILITY_EVIDENCE"}


def test_list_query_count_does_not_grow_with_rows(
    client: TestClient, source: DataSource, db_session: Session
) -> None:
    exchange = Exchange(mic="XNYS", name="NYSE test", timezone="America/New_York")
    db_session.add(exchange)
    db_session.flush()

    def add_company(n: int) -> None:
        row = Company(legal_name=f"Co {n}", display_name=f"Co {n}")
        db_session.add(row)
        db_session.flush()
        security = Security(
            company_id=row.id, security_type=SecurityType.EQUITY, name="S", currency="USD"
        )
        db_session.add(security)
        db_session.flush()
        db_session.add(
            Listing(
                security_id=security.id,
                exchange_id=exchange.id,
                ticker=f"T{n}",
                valid_from=date(2010, 1, 1),
                is_primary=True,
            )
        )
        db_session.flush()
        _event(row, source, db_session, event_date=date(2024, 3, 15))

    def statements() -> int:
        db_session.expire_all()
        count = 0

        def _count(*_args: object) -> None:
            nonlocal count
            count += 1

        bind = db_session.get_bind()
        sa.event.listen(bind, "before_cursor_execute", _count)
        try:
            assert client.get(BASE, params={"limit": 200}).status_code == 200
        finally:
            sa.event.remove(bind, "before_cursor_execute", _count)
        return count

    add_company(1)
    one = statements()
    for n in range(2, 12):
        add_company(n)
    twelve = statements()

    assert twelve == one


def test_non_connectivity_database_errors_are_not_reported_as_503(
    db_session: Session,
) -> None:
    application = create_app(Settings(_env_file=None))

    class _Broken:
        def scalar(self, *_: object, **__: object) -> None:
            raise sa.exc.DataError("SELECT", {}, Exception("boom"))

    application.dependency_overrides[get_session] = _Broken
    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get(BASE)

    assert response.status_code == 500
    assert "boom" not in response.text


def test_invalid_parameters_are_rejected(client: TestClient) -> None:
    assert client.get(BASE, params={"limit": 0}).status_code == 422
    assert client.get(BASE, params={"limit": 201}).status_code == 422
    assert client.get(BASE, params={"offset": -1}).status_code == 422
    assert client.get(BASE, params={"offset": 10**12}).status_code == 422
    assert client.get(BASE, params={"ticker": "   "}).status_code == 422
    assert client.get(BASE, params={"bogus": "x"}).status_code == 422
    inverted = client.get(BASE, params={"date_from": "2024-02-01", "date_to": "2024-01-01"})
    assert inverted.status_code == 422


def test_detail_and_not_found(
    client: TestClient, company: Company, source: DataSource, db_session: Session
) -> None:
    event = _event(company, source, db_session)

    found = client.get(f"{BASE}/{event.id}")
    missing = client.get(f"{BASE}/00000000-0000-0000-0000-000000000000")
    malformed = client.get(f"{BASE}/not-a-uuid")

    assert found.status_code == 200
    assert found.json()["company"]["cik"] == "0000320193"
    assert found.json()["source"] == {
        "name": "api-test-vendor",
        "provider_type": "MARKET_DATA_VENDOR",
    }
    assert missing.status_code == 404
    assert malformed.status_code == 422
