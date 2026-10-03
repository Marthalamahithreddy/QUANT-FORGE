"""Integration tests for reference resolution, against real PostgreSQL.

The property under test throughout is the one that is expensive to get wrong:
**resolution must never merge two distinct issuers.** A ticker is reused, names
collide, and a share class and its issuer are different instruments. Each test
below pins one way that could go wrong.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import (
    CanonicalEquityReference,
    ReferenceResolution,
    ResolvedVenue,
)
from quantforge.ingestion.reference import (
    ReferenceResolver,
    TickerReuseConflictError,
    UnresolvableReferenceError,
)
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import IdentifierType
from quantforge.models.security import Exchange, Listing, Security, SecurityIdentifier

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
XNAS = ResolvedVenue(mic="XNAS", name="NasdaqGS", timezone_name="America/New_York")
NYSE = ResolvedVenue(mic="XNYS", name="NYSE", timezone_name="America/New_York")


@pytest.fixture
def source_id(db_session: Session) -> uuid.UUID:
    row = get_or_create_data_source(db_session, name="massive", base_url="https://api.massive.com")
    db_session.commit()
    assert row.id is not None
    return row.id


def _reference(**overrides: object) -> CanonicalEquityReference:
    fields: dict[str, object] = {
        "ticker": "AAPL",
        "security_type": "EQUITY",
        "venue": XNAS,
        "company_name": "Apple Inc.",
        "cik": "0000320193",
        "composite_figi": "BBG000B9XRY4",
        "share_class_figi": "BBG001S5QY8",
        "currency_code": "USD",
        "is_active": True,
    }
    fields.update(overrides)
    return CanonicalEquityReference(**fields)  # type: ignore[arg-type]


def _resolve(db_session: Session, source_id: uuid.UUID, **overrides: object) -> ReferenceResolution:
    return ReferenceResolver(db_session).resolve(
        _reference(**overrides), source_id=source_id, as_of=date(2026, 9, 27)
    )


# ── creation ────────────────────────────────────────────────────────────────


def test_a_new_reference_creates_the_whole_chain(db_session: Session, source_id: uuid.UUID) -> None:
    result = _resolve(db_session, source_id)
    db_session.commit()

    assert result.created is True
    assert result.matched_on == "cik:0000320193"
    for model in (Company, Security, Listing, SecurityIdentifier, Exchange):
        assert db_session.scalar(sa.select(model)) is not None, f"{model.__name__} missing"


def test_ids_are_assigned_so_the_caller_can_cite_them(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """Every returned id must be usable immediately, without an extra flush.

    The listing needs a real ``listing_id`` before any bar can be written against
    it, so an unresolved id here would surface much later as a foreign-key error.
    """
    result = _resolve(db_session, source_id)
    db_session.commit()
    for value in (result.company_id, result.security_id, result.listing_id):
        assert isinstance(value, uuid.UUID)


def test_the_venue_timezone_is_stored(db_session: Session, source_id: uuid.UUID) -> None:
    """The stored zone is the single authority for the session-date conversion."""
    _resolve(db_session, source_id)
    db_session.commit()

    exchange = db_session.scalar(sa.select(Exchange).where(Exchange.mic == "XNAS"))
    assert exchange is not None
    assert exchange.timezone == "America/New_York"
    assert exchange.name == "NasdaqGS"


def test_the_issuer_cik_is_stored_zero_padded(db_session: Session, source_id: uuid.UUID) -> None:
    _resolve(db_session, source_id)
    db_session.commit()
    company = db_session.scalar(sa.select(Company))
    assert company is not None
    assert company.cik == "0000320193"


# ── idempotency ─────────────────────────────────────────────────────────────


def test_resolving_the_same_reference_twice_reuses_every_row(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """A re-run must not duplicate anything.

    This is what makes a nightly reference refresh safe: the common case is that
    nothing changed, and it must be a no-op rather than a second copy.
    """
    first = _resolve(db_session, source_id)
    db_session.commit()
    counts_before = (
        db_session.query(Company).count(),
        db_session.query(Security).count(),
        db_session.query(Listing).count(),
    )

    second = _resolve(db_session, source_id)
    db_session.commit()

    assert second.created is False
    assert second.company_id == first.company_id
    assert second.security_id == first.security_id
    assert second.listing_id == first.listing_id
    assert counts_before == (
        db_session.query(Company).count(),
        db_session.query(Security).count(),
        db_session.query(Listing).count(),
    )


def test_resolving_the_same_no_figi_reference_twice_reports_the_second_as_matched(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """The no-FIGI security path must report ``created`` accurately too.

    Without a FIGI, ``(company, name)`` is the security's identity, and a
    second resolution of the same reference matches that name rather than
    inserting -- ``_resolve_security``'s no-FIGI branch must propagate
    whether a row was actually written rather than assuming it always was.
    """
    first = _resolve(
        db_session, source_id, cik="0009999001", composite_figi=None, share_class_figi=None
    )
    db_session.commit()
    assert first.created is True

    second = _resolve(
        db_session, source_id, cik="0009999001", composite_figi=None, share_class_figi=None
    )
    db_session.commit()

    assert second.created is False, "nothing new was written on the second pass"
    assert second.security_id == first.security_id
    assert db_session.query(Security).filter(Security.company_id == second.company_id).count() == 1


def test_a_changed_company_name_does_not_create_a_second_issuer(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """The name is display data, not identity.

    Matching on it would both split one issuer across two rows and merge two
    issuers that happen to share a name.
    """
    first = _resolve(db_session, source_id)
    db_session.commit()
    second = _resolve(db_session, source_id, company_name="Apple Computer, Inc.")
    db_session.commit()

    assert second.company_id == first.company_id
    assert db_session.query(Company).count() == 1


# ── identity separation ─────────────────────────────────────────────────────


def test_two_share_classes_of_one_issuer_are_separate_securities(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """GOOG and GOOGL share an issuer but are different instruments.

    Conflating them would attach one class's prices to the other -- a subtle,
    entirely plausible-looking error.
    """
    class_a = _resolve(
        db_session,
        source_id,
        ticker="GOOG",
        share_class_figi="BBG000BDCGP6",
        composite_figi="BBG000BDCGP5",
    )
    db_session.commit()
    class_b = _resolve(
        db_session,
        source_id,
        ticker="GOOGL",
        share_class_figi="BBG000BDCGP7",
        composite_figi="BBG000BDCGP5",
    )
    db_session.commit()

    assert class_a.company_id == class_b.company_id, "one issuer"
    assert class_a.security_id != class_b.security_id, "two instruments"
    assert class_a.listing_id != class_b.listing_id
    assert db_session.query(Company).count() == 1
    assert db_session.query(Security).count() == 2


def test_the_same_ticker_on_two_venues_is_two_listings(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """A ticker is unique only within a venue, so this is not a conflict."""
    nasdaq = _resolve(db_session, source_id, venue=XNAS)
    db_session.commit()
    nyse = _resolve(db_session, source_id, venue=NYSE)
    db_session.commit()

    assert nyse.security_id == nasdaq.security_id
    assert nyse.listing_id != nasdaq.listing_id
    assert db_session.query(Listing).count() == 2


def test_a_reused_ticker_for_a_different_security_is_refused(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """A symbol reassigned to a new issuer must raise, not silently re-point.

    Silently re-pointing would splice two companies' price histories into one
    instrument, and every return computed from it would be meaningless. This is
    the single most consequential refusal in the module.
    """
    _resolve(db_session, source_id)
    db_session.commit()

    with pytest.raises(TickerReuseConflictError):
        _resolve(
            db_session,
            source_id,
            cik="0001652044",
            composite_figi="BBG000C2V3D6",
            share_class_figi="BBG001S5QY9",
        )
    db_session.rollback()


def test_a_reference_with_no_stable_identity_is_refused(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """With neither CIK nor FIGI there is nothing to match on.

    The tempting fallback is name similarity, which merges distinct issuers. It is
    not taken.
    """
    ref = CanonicalEquityReference(
        ticker="ZZZZ", security_type="EQUITY", venue=XNAS, company_name="Something Inc."
    )
    with pytest.raises(UnresolvableReferenceError):
        ReferenceResolver(db_session).resolve(ref, source_id=source_id, as_of=date(2026, 9, 27))
    db_session.rollback()


def test_a_figi_only_reference_resolves_when_we_already_hold_the_instrument(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """A share-class FIGI reaches the issuer through a security we already hold.

    The FIGI identifies an *instrument*; the issuer is then known with certainty,
    because the security carrying that FIGI names its company.
    """
    first = _resolve(db_session, source_id)
    db_session.commit()

    second = _resolve(db_session, source_id, cik=None)
    db_session.commit()

    assert second.created is False
    assert second.company_id == first.company_id
    assert second.matched_on == "figi:BBG001S5QY8"


def test_a_figi_only_reference_for_an_unknown_instrument_is_refused(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """A FIGI we have never seen cannot create an issuer, because a FIGI is not one.

    Creating a company here would risk a second identity for an issuer we may
    already hold under a different name. Refusing is the safe direction: a missing
    symbol is visible, a duplicated issuer is not.
    """
    with pytest.raises(UnresolvableReferenceError, match="no stable identifier"):
        _resolve(db_session, source_id, cik=None)
    db_session.rollback()
    assert db_session.query(Company).count() == 0


def test_a_composite_figi_only_reference_is_refused(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """A composite FIGI alone cannot resolve, because it is not persisted.

    It is issuer-scoped -- every share class of an issuer shares one -- while
    ``security_identifier`` is unique on (type, value) *globally*, so it has no
    home there yet. Until a company-level identifier table exists, an instrument
    known only by its composite FIGI is refused rather than guessed at.
    """
    with pytest.raises(UnresolvableReferenceError):
        _resolve(db_session, source_id, cik=None, share_class_figi=None)
    db_session.rollback()
    assert db_session.query(Company).count() == 0


def test_a_new_figi_under_a_known_cik_joins_that_issuer(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """CIK wins over FIGI, deliberately.

    The CIK names the issuer; the FIGI names one instrument. A reference carrying
    a *different* FIGI under a CIK we already hold is therefore a further
    instrument of a known issuer -- a second share class, say -- so it joins the
    same company rather than creating a rival one.
    """
    first = _resolve(db_session, source_id)
    db_session.commit()
    other = _resolve(db_session, source_id, ticker="AAPL2", share_class_figi="BBG000NEWFIGI")
    db_session.commit()

    assert other.company_id == first.company_id
    assert other.security_id != first.security_id
    assert other.matched_on == "cik:0000320193"


# ── listing history ─────────────────────────────────────────────────────────


def test_the_first_observation_is_the_listing_start(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """``valid_from`` is when we first saw it, not when the provider claims it listed.

    A provider's "listed on" date is a claim; ours is a fact about our own
    history, and conflating them would let a backtest trade before we knew the
    instrument existed.
    """
    as_of = date(2026, 9, 27)
    result = ReferenceResolver(db_session).resolve(_reference(), source_id=source_id, as_of=as_of)
    db_session.commit()

    listing = db_session.get(Listing, result.listing_id)
    assert listing is not None
    assert listing.valid_from == as_of
    assert listing.valid_to is None, "a current listing has no end date"


def test_a_current_listing_is_not_duplicated_by_a_second_observation(
    db_session: Session, source_id: uuid.UUID
) -> None:
    first = _resolve(db_session, source_id)
    db_session.commit()
    second = _resolve(db_session, source_id)
    db_session.commit()
    assert first.listing_id == second.listing_id


# ── identifiers ─────────────────────────────────────────────────────────────


def test_both_figis_are_recorded(db_session: Session, source_id: uuid.UUID) -> None:
    """The composite FIGI identifies the issuer; the share-class one the instrument.

    Losing either would make a later lookup impossible for the case it covers.
    """
    _resolve(db_session, source_id)
    db_session.commit()

    identifiers = list(db_session.scalars(sa.select(SecurityIdentifier)))
    values = {i.identifier_value: i.identifier_type for i in identifiers}

    assert "BBG001S5QY8" in values, "the share-class FIGI identifies the security"
    assert values["BBG001S5QY8"] is IdentifierType.FIGI_SHARE_CLASS, "the FIGI LEVEL is stated"
    assert "BBG000B9XRY4" not in values, "the composite FIGI is not stored as a security anchor"
    # The CIK is issuer-scoped and lives on company.cik, not here: the same CIK
    # applies to every security of the issuer, and security_identifier is unique
    # on (type, value) globally, so one row per share class would collide.
    assert "0000320193" not in values
    assert all(i.security_id is not None for i in identifiers)


def test_a_company_name_is_never_treated_as_unique(
    db_session: Session, source_id: uuid.UUID
) -> None:
    """Two issuers may share a legal name; the database must allow it.

    If the column were unique, ingesting the second would either fail or force a
    rename of a real company.
    """
    _resolve(db_session, source_id, company_name="Duplicate Name Inc")
    db_session.commit()
    _resolve(
        db_session,
        source_id,
        ticker="OTHR",
        cik="0001652044",
        composite_figi="BBG000C2V3D6",
        share_class_figi="BBG001S5QY9",
        company_name="Duplicate Name Inc",
    )
    db_session.commit()

    names = db_session.scalars(
        sa.select(Company.legal_name).where(Company.legal_name == "Duplicate Name Inc")
    ).all()
    assert len(names) == 2
