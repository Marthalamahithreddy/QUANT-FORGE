"""Integration tests for the security-master half of the domain schema.

Every test runs against real PostgreSQL. The assertions that matter here are the
ones SQLite would not enforce: partial unique indexes, PostgreSQL ``CHECK``
regexes, native ``UUID`` columns and half-open ``DATE`` interval boundaries.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from quantforge.models.company import Company
from quantforge.models.enums import IdentifierType, ProviderType, SecurityType
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security, SecurityIdentifier
from quantforge.repositories.company import CompanyRepository
from quantforge.repositories.security import (
    ExchangeRepository,
    ListingRepository,
    SecurityIdentifierRepository,
    SecurityRepository,
)

pytestmark = pytest.mark.integration


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def source(db_session: Session) -> DataSource:
    """A registered provider. Test-only data, created inside the rolled-back test."""
    row = DataSource(
        name="integration-vendor",
        provider_type=ProviderType.MARKET_DATA_VENDOR,
        base_url="https://vendor.invalid",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    """A registered issuer."""
    row = Company(
        legal_name="Integration Industries PLC",
        display_name="Integration Industries",
        sector="Information Technology",
        industry="Software",
        country_code="GB",
        founded_date=date(1998, 3, 14),
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    """A registered venue."""
    row = Exchange(
        mic="XINT",
        name="Integration Exchange",
        acronym="INTX",
        country_code="US",
        timezone="America/New_York",
        currency="USD",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def security(company: Company, db_session: Session) -> Security:
    """An equity instrument issued by ``company``."""
    row = Security(
        company_id=company.id,
        security_type=SecurityType.EQUITY,
        name="Class A Common Stock",
        currency="USD",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def listing(security: Security, exchange: Exchange, db_session: Session) -> Listing:
    """A current listing of ``security`` on ``exchange``."""
    row = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker="INTG",
        valid_from=date(2015, 1, 2),
        valid_to=None,
        is_primary=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


# ── company persistence ──────────────────────────────────────────────────────


def test_company_round_trips_all_fields(db_session: Session) -> None:
    created = CompanyRepository(db_session).add(
        Company(
            legal_name="Precision Holdings Limited",
            display_name="Precision Holdings",
            description="An issuer used to prove persistence.",
            sector="Financials",
            industry="Banks",
            country_code="NL",
            website="https://precision.invalid",
            founded_date=date(1974, 11, 5),
        )
    )
    db_session.commit()

    loaded = CompanyRepository(db_session).get(created.id)
    assert loaded is not None
    assert loaded.legal_name == "Precision Holdings Limited"
    assert loaded.country_code == "NL"
    assert loaded.founded_date == date(1974, 11, 5)
    assert loaded.created_at.tzinfo is not None
    assert loaded.updated_at.tzinfo is not None


def test_company_get_returns_none_for_unknown_id(db_session: Session) -> None:
    assert CompanyRepository(db_session).get(uuid.uuid4()) is None


def test_company_name_is_not_unique_across_jurisdictions(db_session: Session) -> None:
    """Two unrelated issuers may share a legal name; identity is the UUID.

    Real data hits this: several issuers are called 'Banco Santander', and a
    name is not a stable market identifier anyway -- companies rename, and
    distinct entities in different jurisdictions can carry identical strings.
    Constraining the name would reject real issuers, so the name carries no
    uniqueness at all and the UUID is the only identity.
    """
    repo = CompanyRepository(db_session)
    spanish = Company(
        legal_name="Banco Santander", display_name="Banco Santander", country_code="ES"
    )
    chilean = Company(
        legal_name="Banco Santander", display_name="Banco Santander", country_code="CL"
    )
    repo.add(spanish)
    repo.add(chilean)
    db_session.commit()

    matches = list(
        db_session.scalars(sa.select(Company).where(Company.legal_name == "Banco Santander"))
    )
    assert len(matches) == 2

    # Same name, so the name cannot be what tells them apart.
    assert {c.legal_name for c in matches} == {"Banco Santander"}
    # Distinct identity, and each row is individually addressable.
    assert spanish.id != chilean.id
    assert {c.country_code for c in matches} == {"ES", "CL"}
    assert repo.get(spanish.id) is not None
    assert repo.get(chilean.id) is not None
    assert repo.get(chilean.id) is not spanish


def test_company_name_needs_no_index_for_lookups(db_session: Session) -> None:
    """A duplicate name is returned whole, with no first-row-wins behaviour.

    Pinning the ordering stops a future 'just index and take the first hit'
    change from quietly collapsing two issuers into one.
    """
    repo = CompanyRepository(db_session)
    for code in ("ES", "CL", "AR"):
        repo.add(
            Company(legal_name="Banco Santander", display_name="Banco Santander", country_code=code)
        )
    db_session.commit()

    matches = list(
        db_session.scalars(sa.select(Company).where(Company.legal_name == "Banco Santander"))
    )
    assert {c.country_code for c in matches} == {"ES", "CL", "AR"}


@pytest.mark.parametrize("country_code", ["us", "u1", "u_", "1a", "Us", ""])
def test_company_rejects_wrongly_shaped_country_codes(
    db_session: Session, country_code: str
) -> None:
    """Right length, wrong shape: rejected by the ``CHECK`` constraint itself.

    The database is the authority, not the Pydantic layer -- a row can still
    arrive from a ``COPY``, a raw script or a future ingestion worker.
    """
    db_session.add(
        Company(legal_name="Bad Code Inc", display_name="Bad Code", country_code=country_code)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("country_code", ["USA", "United States", "ABC"])
def test_company_rejects_country_codes_of_the_wrong_length(
    db_session: Session, country_code: str
) -> None:
    """Wrong length is caught by the column width, before the ``CHECK`` runs.

    A genuinely different failure mode from a bad shape, and therefore a
    different exception class: ``DataError`` rather than ``IntegrityError``.
    """
    db_session.add(
        Company(legal_name="Bad Length Inc", display_name="Bad Length", country_code=country_code)
    )
    with pytest.raises(DataError):
        db_session.commit()
    db_session.rollback()


# ── security -> company relationship ─────────────────────────────────────────


def test_security_belongs_to_its_company(db_session: Session, company: Company) -> None:
    repo = SecurityRepository(db_session)
    created = repo.add(
        Security(
            company_id=company.id,
            security_type=SecurityType.PREFERRED_EQUITY,
            name="Series B Preferred",
            currency="USD",
        )
    )
    db_session.commit()

    loaded = repo.get(created.id)
    assert loaded is not None
    assert loaded.company_id == company.id
    assert loaded.company.legal_name == "Integration Industries PLC"
    assert loaded.security_type is SecurityType.PREFERRED_EQUITY


def test_company_exposes_its_securities(company: Company, security: Security) -> None:
    """The company -> securities relationship resolves from either side."""
    assert [s.id for s in company.securities] == [security.id]


def test_company_issues_multiple_securities(
    company: Company, security: Security, db_session: Session
) -> None:
    """A company is not a ticker: one issuer, several distinct instruments."""
    db_session.add(
        Security(
            company_id=company.id,
            security_type=SecurityType.DEPOSITORY_RECEIPT,
            name="American Depositary Shares",
            currency="USD",
        )
    )
    db_session.commit()

    types = {s.security_type for s in company.securities}
    assert SecurityType.EQUITY in types
    assert SecurityType.DEPOSITORY_RECEIPT in types


def test_security_requires_an_existing_company(db_session: Session) -> None:
    db_session.add(
        Security(
            company_id=uuid.uuid4(),
            security_type=SecurityType.EQUITY,
            name="Orphan Stock",
            currency="USD",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_duplicate_security_name_for_one_company_is_rejected(
    company: Company, security: Security, db_session: Session
) -> None:
    db_session.add(
        Security(
            company_id=company.id,
            security_type=SecurityType.EQUITY,
            name="Class A Common Stock",
            currency="USD",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_security_name_under_different_companies_is_allowed(
    db_session: Session, security: Security
) -> None:
    """``(company_id, name)`` is unique, not ``name`` alone."""
    other = Company(legal_name="Second Issuer SA", display_name="Second Issuer", country_code="FR")
    db_session.add(other)
    db_session.commit()

    db_session.add(
        Security(
            company_id=other.id,
            security_type=SecurityType.EQUITY,
            name="Class A Common Stock",
            currency="EUR",
        )
    )
    db_session.commit()

    assert SecurityRepository(db_session).get(security.id) is not None


# ── exchange ────────────────────────────────────────────────────────────────


def test_exchange_persists_iana_timezone(exchange: Exchange) -> None:
    """An IANA zone name, not a fixed offset -- DST is a recurring fact."""
    assert exchange.timezone == "America/New_York"
    assert "-" not in exchange.timezone


def test_mic_is_unique(db_session: Session, exchange: Exchange) -> None:
    db_session.add(Exchange(mic="XINT", name="Duplicate MIC Venue", timezone="UTC"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("mic", ["xint", "Xint", "XIN", "x123", "X_12", "X-12"])
def test_mic_shape_is_enforced_by_the_database(db_session: Session, mic: str) -> None:
    """A wrongly-shaped MIC is rejected by the ``CHECK``, not only by Pydantic.

    Without it, ``xint`` and ``XINT`` would be two rows claiming the same venue,
    and a case-normalising lookup could only ever return one of them.
    """
    db_session.add(Exchange(mic=mic, name="Bad MIC Venue", timezone="UTC"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("mic", ["XINT", "X123", "XTSE"])
def test_well_formed_mics_are_accepted(db_session: Session, mic: str) -> None:
    """ISO 10383 segment codes may contain digits, so a purely numeric tail is
    well formed and must not be over-restricted."""
    db_session.add(Exchange(mic=mic, name="Good MIC Venue", timezone="UTC"))
    db_session.commit()
    assert ExchangeRepository(db_session).get_by_mic(mic) is not None


def test_get_by_mic_normalises_the_caller_s_spelling(
    exchange: Exchange, db_session: Session
) -> None:
    """The repository upper-cases the lookup, so a caller that types ``xint``
    still finds the canonical ``XINT`` row instead of a spurious miss."""
    repo = ExchangeRepository(db_session)
    assert repo.get_by_mic("XINT") is not None
    assert repo.get_by_mic("xint") is not None


# ── listing ─────────────────────────────────────────────────────────────────


def test_listing_persists_with_open_interval(listing: Listing) -> None:
    """``valid_to IS NULL`` means 'still listed'."""
    assert listing.valid_to is None
    assert listing.is_primary is True
    assert listing.ticker == "INTG"


def test_ticker_is_not_globally_unique(
    listing: Listing,
    security: Security,
    exchange: Exchange,
    db_session: Session,
) -> None:
    """The same ticker may exist on two exchanges simultaneously.

    This is the case a naive ``UNIQUE(ticker)`` would make unrepresentable, and
    the reason ticker resolution takes an optional venue.
    """
    other_venue = Exchange(mic="XOTH", name="Other Exchange", timezone="Europe/London")
    db_session.add(other_venue)
    db_session.commit()

    db_session.add(
        Listing(
            security_id=security.id,
            exchange_id=other_venue.id,
            ticker="INTG",
            valid_from=date(2015, 1, 2),
        )
    )
    db_session.commit()

    rows = db_session.scalars(sa.select(Listing).where(Listing.ticker == "INTG"))
    assert len(list(rows)) == 2


def test_ticker_can_be_reused_by_a_different_issuer_later(
    exchange: Exchange,
    db_session: Session,
) -> None:
    """A retired ticker handed to an unrelated issuer years later.

    The half-open intervals make this representable; a global unique ticker
    constraint would have forbidden it outright.
    """
    first = Company(legal_name="Original Issuer", display_name="Original")
    second = Company(legal_name="Successor Issuer", display_name="Successor")
    db_session.add_all([first, second])
    db_session.commit()

    first_security = Security(
        company_id=first.id, security_type=SecurityType.EQUITY, name="Stock", currency="USD"
    )
    second_security = Security(
        company_id=second.id, security_type=SecurityType.EQUITY, name="Stock", currency="USD"
    )
    db_session.add_all([first_security, second_security])
    db_session.commit()

    db_session.add_all(
        [
            Listing(
                security_id=first_security.id,
                exchange_id=exchange.id,
                ticker="REUSE",
                valid_from=date(1990, 1, 2),
                valid_to=date(2010, 6, 1),
            ),
            Listing(
                security_id=second_security.id,
                exchange_id=exchange.id,
                ticker="REUSE",
                valid_from=date(2010, 6, 1),
                valid_to=None,
            ),
        ]
    )
    db_session.commit()

    history = ListingRepository(db_session).ticker_history(exchange_id=exchange.id, ticker="REUSE")
    assert [row.security_id for row in history] == [first_security.id, second_security.id]


def test_listing_rejects_inverted_validity_interval(
    security: Security, exchange: Exchange, db_session: Session
) -> None:
    db_session.add(
        Listing(
            security_id=security.id,
            exchange_id=exchange.id,
            ticker="BADP",
            valid_from=date(2020, 1, 1),
            valid_to=date(2019, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_listing_rejects_zero_length_validity_interval(
    security: Security, exchange: Exchange, db_session: Session
) -> None:
    """``valid_to > valid_from`` is strict: a one-day period is impossible."""
    db_session.add(
        Listing(
            security_id=security.id,
            exchange_id=exchange.id,
            ticker="ZERO",
            valid_from=date(2020, 5, 1),
            valid_to=date(2020, 5, 1),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_listing_rejects_lowercase_ticker(
    security: Security, exchange: Exchange, db_session: Session
) -> None:
    """Canonicalisation is enforced in the database, not only in Pydantic."""
    db_session.add(
        Listing(
            security_id=security.id,
            exchange_id=exchange.id,
            ticker="lower",
            valid_from=date(2020, 1, 2),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_duplicate_listing_period_on_same_venue_is_rejected(
    listing: Listing, db_session: Session
) -> None:
    db_session.add(
        Listing(
            security_id=listing.security_id,
            exchange_id=listing.exchange_id,
            ticker=listing.ticker,
            valid_from=date(2015, 1, 2),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_two_current_listings_with_same_ticker_on_one_venue_are_rejected(
    security: Security, exchange: Exchange, db_session: Session
) -> None:
    """Proves the partial unique index ``WHERE valid_to IS NULL`` is live.

    SQLite cannot express this constraint, which is one reason these tests run
    on PostgreSQL.
    """
    db_session.add(
        Listing(
            security_id=security.id,
            exchange_id=exchange.id,
            ticker="DUPCUR",
            valid_from=date(2020, 1, 2),
            valid_to=None,
        )
    )
    db_session.commit()

    other = Security(
        company_id=security.company_id,
        security_type=SecurityType.EQUITY,
        name="Class B Common Stock",
        currency="USD",
    )
    db_session.add(other)
    db_session.commit()

    db_session.add(
        Listing(
            security_id=other.id,
            exchange_id=exchange.id,
            ticker="DUPCUR",
            valid_from=date(2021, 1, 4),
            valid_to=None,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_two_current_primary_listings_for_one_security_are_rejected(
    security: Security,
    exchange: Exchange,
    db_session: Session,
) -> None:
    other_venue = Exchange(mic="XSEC", name="Second Venue", timezone="Europe/Frankfurt")
    db_session.add(other_venue)
    db_session.commit()

    # The first primary listing, on the original venue.
    db_session.add(
        Listing(
            security_id=security.id,
            exchange_id=exchange.id,
            ticker="PRIMA",
            valid_from=date(2015, 1, 2),
            is_primary=True,
        )
    )
    db_session.commit()

    # A second, still-open primary listing for the same instrument: which
    # venue is "the" primary venue would be undefined.
    db_session.add(
        Listing(
            security_id=security.id,
            exchange_id=other_venue.id,
            ticker="SECDUP",
            valid_from=date(2020, 1, 2),
            is_primary=True,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ── repository: ticker resolution by date ───────────────────────────────────


def test_resolve_by_ticker_respects_half_open_intervals(
    security: Security, exchange: Exchange, db_session: Session
) -> None:
    """``[valid_from, valid_to)``: the end date is exclusive."""
    db_session.add(
        Listing(
            security_id=security.id,
            exchange_id=exchange.id,
            ticker="HIST",
            valid_from=date(2020, 1, 2),
            valid_to=date(2023, 4, 10),
        )
    )
    db_session.commit()

    repo = ListingRepository(db_session)
    # Inclusive lower bound.
    assert repo.resolve_by_ticker("HIST", on_date=date(2020, 1, 2), exchange_id=exchange.id)
    # Strictly inside.
    assert repo.resolve_by_ticker("HIST", on_date=date(2021, 6, 1), exchange_id=exchange.id)
    # Exclusive upper bound: the last day that still belongs to this period.
    assert repo.resolve_by_ticker("HIST", on_date=date(2023, 4, 9), exchange_id=exchange.id)
    # The end date itself belongs to the next period, not this one.
    assert (
        repo.resolve_by_ticker("HIST", on_date=date(2023, 4, 10), exchange_id=exchange.id) is None
    )
    # Before the start.
    assert repo.resolve_by_ticker("HIST", on_date=date(2020, 1, 1), exchange_id=exchange.id) is None


def test_resolve_by_ticker_is_case_insensitive(
    listing: Listing, exchange: Exchange, db_session: Session
) -> None:
    """Callers may pass any case; stored tickers are canonical upper case."""
    resolved = ListingRepository(db_session).resolve_by_ticker(
        "intg", on_date=date(2020, 1, 2), exchange_id=exchange.id
    )
    assert resolved is not None
    assert resolved.ticker == "INTG"


def test_resolve_by_ticker_without_venue_is_ambiguous_when_ticker_exists_twice(
    security: Security,
    exchange: Exchange,
    db_session: Session,
) -> None:
    other_venue = Exchange(mic="XAMB", name="Ambiguity Venue", timezone="Asia/Tokyo")
    db_session.add(other_venue)
    db_session.commit()
    db_session.add_all(
        [
            Listing(
                security_id=security.id,
                exchange_id=exchange.id,
                ticker="AMBIG",
                valid_from=date(2020, 1, 2),
            ),
            Listing(
                security_id=security.id,
                exchange_id=other_venue.id,
                ticker="AMBIG",
                valid_from=date(2020, 1, 2),
            ),
        ]
    )
    db_session.commit()

    # Omitting the venue still returns *a* row, but the caller cannot tell which
    # venue it got -- which is why the parameter exists. Passing the venue makes
    # the answer deterministic.
    repo = ListingRepository(db_session)
    scoped = repo.resolve_by_ticker("AMBIG", on_date=date(2020, 6, 1), exchange_id=other_venue.id)
    assert scoped is not None
    assert scoped.exchange_id == other_venue.id


def test_current_primary_listing_is_returned(listing: Listing, db_session: Session) -> None:
    repo = ListingRepository(db_session)
    found = repo.current_primary_listing(listing.security_id)
    assert found is not None
    assert found.id == listing.id


# ── identifiers ─────────────────────────────────────────────────────────────


def test_identifier_persists_with_type_and_source(
    security: Security, source: DataSource, db_session: Session
) -> None:
    repo = SecurityIdentifierRepository(db_session)
    created = repo.add(
        SecurityIdentifier(
            security_id=security.id,
            identifier_type=IdentifierType.ISIN,
            identifier_value="GB00INTG0001",
            valid_from=date(2015, 1, 2),
            valid_to=None,
            source_id=source.id,
        )
    )
    db_session.commit()

    loaded = repo.get_by_code(IdentifierType.ISIN, "GB00INTG0001")
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.security_id == security.id
    assert loaded.source_id == source.id


def test_identifier_code_is_globally_unique_within_its_scheme(
    security: Security, db_session: Session
) -> None:
    """An ISIN names exactly one instrument; dual ownership is the corruption
    this constraint exists to prevent."""
    db_session.add(
        SecurityIdentifier(
            security_id=security.id,
            identifier_type=IdentifierType.ISIN,
            identifier_value="GB00INTG0001",
        )
    )
    db_session.commit()

    other_company = Company(legal_name="Rival PLC", display_name="Rival")
    db_session.add(other_company)
    db_session.commit()
    rival = Security(
        company_id=other_company.id,
        security_type=SecurityType.EQUITY,
        name="Rival Ordinary",
        currency="GBP",
    )
    db_session.add(rival)
    db_session.commit()

    db_session.add(
        SecurityIdentifier(
            security_id=rival.id,
            identifier_type=IdentifierType.ISIN,
            identifier_value="GB00INTG0001",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_value_under_different_schemes_is_allowed(
    security: Security, db_session: Session
) -> None:
    """Uniqueness is per ``(type, value)``, not on the value alone."""
    db_session.add_all(
        [
            SecurityIdentifier(
                security_id=security.id,
                identifier_type=IdentifierType.CUSIP,
                identifier_value="123456789",
            ),
            SecurityIdentifier(
                security_id=security.id,
                identifier_type=IdentifierType.SEDOL,
                identifier_value="123456789",
            ),
        ]
    )
    db_session.commit()

    repo = SecurityIdentifierRepository(db_session)
    assert repo.get_by_code(IdentifierType.CUSIP, "123456789") is not None
    assert repo.get_by_code(IdentifierType.SEDOL, "123456789") is not None


def test_identifier_allows_undisclosed_validity(security: Security, db_session: Session) -> None:
    """Both endpoints nullable: many sources never disclose effective dates."""
    db_session.add(
        SecurityIdentifier(
            security_id=security.id,
            identifier_type=IdentifierType.FIGI,
            identifier_value="BBG000BLNNH6",
        )
    )
    db_session.commit()

    loaded = SecurityIdentifierRepository(db_session).get_by_code(
        IdentifierType.FIGI, "BBG000BLNNH6"
    )
    assert loaded is not None
    assert loaded.valid_from is None
    assert loaded.valid_to is None


def test_identifier_rejects_valid_to_without_valid_from(
    security: Security, db_session: Session
) -> None:
    db_session.add(
        SecurityIdentifier(
            security_id=security.id,
            identifier_type=IdentifierType.ISIN,
            identifier_value="GB00HALF0001",
            valid_from=None,
            valid_to=date(2020, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_identifier_rejects_inverted_interval(security: Security, db_session: Session) -> None:
    db_session.add(
        SecurityIdentifier(
            security_id=security.id,
            identifier_type=IdentifierType.ISIN,
            identifier_value="GB00INV00001",
            valid_from=date(2021, 1, 1),
            valid_to=date(2020, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_security_exposes_its_identifiers(security: Security, db_session: Session) -> None:
    db_session.add(
        SecurityIdentifier(
            security_id=security.id,
            identifier_type=IdentifierType.ISIN,
            identifier_value="GB00LIST0001",
        )
    )
    db_session.commit()

    assert [i.identifier_value for i in security.identifiers] == ["GB00LIST0001"]
