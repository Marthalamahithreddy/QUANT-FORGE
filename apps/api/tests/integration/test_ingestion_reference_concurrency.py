"""Concurrency integration tests for reference resolution, against real PostgreSQL.

``tests/integration/test_ingestion_reference.py`` pins the *sequential*
behaviour of :class:`~quantforge.ingestion.reference.ReferenceResolver`: run it
twice and the second call matches instead of duplicating. This file pins the
same guarantee under real concurrency, the way
``tests/integration/test_ingestion_sources.py`` already does for
:func:`~quantforge.ingestion.sources.get_or_create_data_source`.

Reproduced deterministically, not hoped for
--------------------------------------------
A bare ``threading.Barrier`` only synchronises when two threads *start*; it
says nothing about whether their writes actually collide at the database. In
practice the two threads' SQL round-trips rarely land in the same
microsecond, so a barrier-only test would overwhelmingly exercise "the second
caller's own SELECT already sees the first caller's committed row" -- the
ordinary sequential path -- rather than the ``ON CONFLICT DO NOTHING`` *loser*
branch this hardening pass actually added.

So every test here forces the collision explicitly: the "winner" resolves a
reference and is held, via an event, at the point where its row(s) are
inserted but *not yet committed*. Only then is the "loser" released to attempt
a colliding resolution. Under ``READ COMMITTED``, the loser's own
``INSERT ... ON CONFLICT DO NOTHING`` genuinely blocks on the winner's
uncommitted row lock -- not a guess, a real wait -- and only resolves (as "no
row inserted") once the winner commits.

Isolating which identity actually collides
--------------------------------------------
``resolve()`` walks exchange -> company -> security -> listing in one call.
If two racing references shared *every* identity, the earliest stage
(exchange) would be the only one to ever see a real conflict: by the time its
loser unblocks, the winner has already committed everything else, so every
later stage would simply find already-committed rows through its ordinary
match path. To actually exercise each stage's own conflict-recovery code, each
test below shares *only* the one identity it is pinning and gives the two
sides distinct values everywhere else, so the collision happens exactly where
intended and nowhere earlier.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalEquityReference, ResolvedVenue
from quantforge.ingestion.reference import ReferenceResolver
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security, SecurityIdentifier

pytestmark = pytest.mark.integration

AS_OF = date(2026, 9, 27)
SOURCE_NAME = "massive-reference-race"

#: Venues, CIKs and tickers this file owns exclusively, so its cleanup cannot
#: collide with rows other integration tests create inside a rolled-back
#: savepoint (this file needs real commits, so nothing here is rolled back
#: automatically). Separate MICs/CIKs per test keep each race isolated to the
#: one identity it is pinning -- see the module docstring.
RACE_MIC = "RACE"
RACE_MIC_A = "RACA"
RACE_MIC_B = "RACB"
RACE_CIK = "9999999901"
RACE_CIK_A = "9999999902"
RACE_CIK_B = "9999999903"
RACE_TICKER_A = "RACEAA"
RACE_TICKER_B = "RACEBB"

#: Upper bound, in seconds, the winner holds its transaction open after its
#: insert so the loser's blocking statement has time to actually reach and
#: start waiting on the lock. Generous relative to a local INSERT round-trip;
#: the ``timeout=`` on every ``.wait()``/``.result()`` below is what actually
#: bounds the test if this assumption is ever wrong.
_HOLD_SECONDS = 0.3

Committed = Callable[[], Session]


@pytest.fixture
def committed(integration_engine: Engine) -> Iterator[Committed]:
    """Sessions that really commit, cleaned up afterwards in FK order.

    Mirrors the fixture of the same name in ``test_ingestion_sources.py``: two
    sessions must see each other's writes, which the savepoint-rolled-back
    ``db_session`` fixture used everywhere else cannot provide.
    """

    def factory() -> Session:
        return Session(integration_engine)

    yield factory

    ciks = (RACE_CIK, RACE_CIK_A, RACE_CIK_B)
    mics = (RACE_MIC, RACE_MIC_A, RACE_MIC_B)
    with Session(integration_engine) as cleanup:
        cleanup.execute(
            sa.delete(Listing).where(
                Listing.exchange_id.in_(sa.select(Exchange.id).where(Exchange.mic.in_(mics)))
            )
        )
        cleanup.execute(
            sa.delete(SecurityIdentifier).where(
                SecurityIdentifier.security_id.in_(
                    sa.select(Security.id).where(
                        Security.company_id.in_(sa.select(Company.id).where(Company.cik.in_(ciks)))
                    )
                )
            )
        )
        cleanup.execute(
            sa.delete(Security).where(
                Security.company_id.in_(sa.select(Company.id).where(Company.cik.in_(ciks)))
            )
        )
        cleanup.execute(sa.delete(Company).where(Company.cik.in_(ciks)))
        cleanup.execute(sa.delete(Exchange).where(Exchange.mic.in_(mics)))
        cleanup.execute(sa.delete(DataSource).where(DataSource.name == SOURCE_NAME))
        cleanup.commit()


def _reference(ticker: str, *, mic: str, cik: str, figi: str) -> CanonicalEquityReference:
    return CanonicalEquityReference(
        ticker=ticker,
        security_type="EQUITY",
        venue=ResolvedVenue(mic=mic, name="Race Test Venue", timezone_name="America/New_York"),
        company_name="Race Test Issuer",
        cik=cik,
        composite_figi=f"BBG000{cik[-4:]}CF",
        share_class_figi=figi,
        currency_code="USD",
        is_active=True,
    )


@pytest.fixture(autouse=True)
def _source_id(committed: Committed) -> uuid.UUID:
    """Register the shared provider once, sequentially, before any race.

    Keeps every test below focused on the single identity it is pinning:
    ``get_or_create_data_source`` has its own dedicated concurrency coverage
    in ``test_ingestion_sources.py`` and is not what these tests are about.
    """
    with committed() as session:
        source = get_or_create_data_source(session, name=SOURCE_NAME)
        session.commit()
        return source.id


def _race_resolve(
    *,
    winner_ref: CanonicalEquityReference,
    loser_ref: CanonicalEquityReference,
    committed: Committed,
    source_id: uuid.UUID,
) -> tuple[tuple[uuid.UUID, uuid.UUID, uuid.UUID], tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """Resolve two references concurrently, forcing a genuine write race.

    Returns each side's ``(company_id, security_id, listing_id)``. The winner
    inserts and is held *uncommitted* until the loser's own insert has had time
    to start blocking on the resulting lock; only then does the winner commit,
    which is the exact moment the loser's ``ON CONFLICT DO NOTHING`` resolves.
    """
    row_inserted = threading.Event()
    winner_result: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []
    loser_result: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []

    def run_winner() -> None:
        with committed() as session:
            result = ReferenceResolver(session).resolve(
                winner_ref, source_id=source_id, as_of=AS_OF
            )
            winner_result.append((result.company_id, result.security_id, result.listing_id))
            row_inserted.set()
            # Give the loser's blocking statement time to actually start
            # waiting on the row lock before this transaction releases it.
            time.sleep(_HOLD_SECONDS)
            session.commit()

    def run_loser() -> None:
        assert row_inserted.wait(timeout=30), "winner never reached its insert"
        with committed() as session:
            # Blocks here, on the real row lock, until run_winner() commits.
            result = ReferenceResolver(session).resolve(loser_ref, source_id=source_id, as_of=AS_OF)
            loser_result.append((result.company_id, result.security_id, result.listing_id))
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_winner), pool.submit(run_loser)]
        for future in futures:
            future.result(timeout=60)

    return winner_result[0], loser_result[0]


def test_concurrent_identical_company_resolution_creates_exactly_one_company(
    committed: Committed, _source_id: uuid.UUID
) -> None:
    """Two processes resolving the same CIK at once must not create two issuers.

    Distinct exchange and distinct FIGI on each side isolates the collision to
    ``_resolve_company``'s ``uq_company_cik`` path specifically -- without
    this, the two sides' *exchanges* would collide first (exchange is resolved
    before company in ``resolve()``), and this test would exercise the
    exchange loser-path instead of the company one.
    """
    winner_ref = _reference(RACE_TICKER_A, mic=RACE_MIC_A, cik=RACE_CIK, figi="BBG000RACEA1")
    loser_ref = _reference(RACE_TICKER_B, mic=RACE_MIC_B, cik=RACE_CIK, figi="BBG000RACEB1")
    winner, loser = _race_resolve(
        winner_ref=winner_ref, loser_ref=loser_ref, committed=committed, source_id=_source_id
    )
    assert winner[0] == loser[0], "both callers must resolve to the same issuer"
    with Session(committed().bind) as check:
        assert (
            check.scalar(
                sa.select(sa.func.count()).select_from(Company).where(Company.cik == RACE_CIK)
            )
            == 1
        )


def test_concurrent_identical_exchange_resolution_creates_exactly_one_exchange(
    committed: Committed, _source_id: uuid.UUID
) -> None:
    """Two processes resolving the same venue MIC at once must not create two venues.

    Distinct CIK and FIGI on each side keeps the collision isolated to
    ``_resolve_exchange``'s ``mic`` path.
    """
    winner_ref = _reference(RACE_TICKER_A, mic=RACE_MIC, cik=RACE_CIK_A, figi="BBG000RACEA2")
    loser_ref = _reference(RACE_TICKER_B, mic=RACE_MIC, cik=RACE_CIK_B, figi="BBG000RACEB2")
    _race_resolve(
        winner_ref=winner_ref, loser_ref=loser_ref, committed=committed, source_id=_source_id
    )
    with Session(committed().bind) as check:
        assert (
            check.scalar(
                sa.select(sa.func.count()).select_from(Exchange).where(Exchange.mic == RACE_MIC)
            )
            == 1
        )


def test_concurrent_identical_security_resolution_creates_exactly_one_security(
    committed: Committed, _source_id: uuid.UUID
) -> None:
    """Two processes resolving the same share-class FIGI at once must not create two instruments.

    Distinct exchange *and* distinct CIK on each side means neither the
    exchange nor the company collides -- each side gets its own venue and its
    own (synthetic, for this test only) issuer -- so the collision is forced
    specifically onto ``_insert_security``'s ``SAVEPOINT``: the loser creates
    its own ``Security`` row under its own company without incident, then
    blocks adding a ``SecurityIdentifier`` for the shared FIGI, which is
    globally unique. It must resolve that collision by reusing the winner's
    security via ``_security_for_figi`` -- never a raw ``IntegrityError``
    escaping to abort the loser's resolution.
    """
    shared_figi = "BBG000RACESH"
    winner_ref = _reference(RACE_TICKER_A, mic=RACE_MIC_A, cik=RACE_CIK_A, figi=shared_figi)
    loser_ref = _reference(RACE_TICKER_B, mic=RACE_MIC_B, cik=RACE_CIK_B, figi=shared_figi)
    winner, loser = _race_resolve(
        winner_ref=winner_ref, loser_ref=loser_ref, committed=committed, source_id=_source_id
    )
    assert winner[1] == loser[1], "both callers must resolve to the same instrument"
    with Session(committed().bind) as check:
        count = check.scalar(
            sa.select(sa.func.count())
            .select_from(SecurityIdentifier)
            .where(SecurityIdentifier.identifier_value == shared_figi)
        )
        assert count == 1, "the FIGI must be recorded exactly once, not once per racer"


def test_concurrent_different_securities_sharing_a_synthesised_name_both_survive(
    committed: Committed, _source_id: uuid.UUID
) -> None:
    """Two *different* instruments that happen to collide on synthesised name.

    Unlike every other test in this file, this one is not about preventing a
    duplicate -- it is about ``_insert_security``'s suffix-retry recursion,
    which a prior review found was never exercised by any test: two
    resolutions under the *same* company and ticker (so both compute the
    identical ``"{ticker} {label}"`` name) but with genuinely *different*
    FIGIs must both survive, ending up as two distinct ``Security`` rows,
    the loser's automatically suffixed with its own FIGI rather than either
    raising or silently merging into the other.
    """
    company_id, _exchange_id = _seed_company_and_exchange(committed)

    winner_ref = _reference(RACE_TICKER_A, mic=RACE_MIC, cik=RACE_CIK, figi="BBG000RACESA")
    loser_ref = _reference(RACE_TICKER_A, mic=RACE_MIC, cik=RACE_CIK, figi="BBG000RACESB")

    row_inserted = threading.Event()
    winner_seen: list[tuple[uuid.UUID, str]] = []
    loser_seen: list[tuple[uuid.UUID, str]] = []

    def run_winner() -> None:
        with committed() as session:
            security, _created = ReferenceResolver(session)._resolve_security(
                winner_ref, company_id=company_id, source_id=_source_id
            )
            winner_seen.append((security.id, security.name))
            row_inserted.set()
            time.sleep(_HOLD_SECONDS)
            session.commit()

    def run_loser() -> None:
        assert row_inserted.wait(timeout=30), "winner never reached its insert"
        with committed() as session:
            # Blocks on the winner's uncommitted (company_id, name) row, then
            # -- once unblocked -- must hit the suffix-retry path rather than
            # raise, since this really is a different instrument (own FIGI).
            security, _created = ReferenceResolver(session)._resolve_security(
                loser_ref, company_id=company_id, source_id=_source_id
            )
            loser_seen.append((security.id, security.name))
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_winner), pool.submit(run_loser)]
        for future in futures:
            future.result(timeout=60)

    (winner_id, winner_name) = winner_seen[0]
    (loser_id, loser_name) = loser_seen[0]
    assert winner_id != loser_id, "two genuinely different instruments, not merged"
    assert winner_name != loser_name, "the collision must be resolved by suffixing, not by reuse"
    assert loser_name.startswith(winner_name), (
        "the suffix is appended to, not instead of, the shared name"
    )

    with Session(committed().bind) as check:
        assert (
            check.scalar(
                sa.select(sa.func.count())
                .select_from(Security)
                .where(Security.company_id == company_id)
            )
            == 2
        )
        figis = set(
            check.scalars(
                sa.select(SecurityIdentifier.identifier_value).where(
                    SecurityIdentifier.security_id.in_([winner_id, loser_id])
                )
            )
        )
        assert figis == {"BBG000RACESA", "BBG000RACESB"}, "each instrument keeps its own FIGI"


def _seed_company_and_exchange(committed: Committed) -> tuple[uuid.UUID, uuid.UUID]:
    """Sequentially resolve just the exchange and company this file races under.

    Committed outside any race so the security-level tests can race in
    isolation, without also exercising (and thereby conflating results with)
    the exchange/company conflict-recovery paths covered elsewhere in this
    file.
    """
    seed_ref = _reference(RACE_TICKER_B, mic=RACE_MIC, cik=RACE_CIK, figi="BBG000RACESEED")
    with committed() as session:
        resolver = ReferenceResolver(session)
        exchange, _ = resolver._resolve_exchange(seed_ref)
        company, _, _ = resolver._resolve_company(seed_ref)
        session.commit()
        return company.id, exchange.id


def test_concurrent_identical_listing_resolution_creates_exactly_one_listing(
    committed: Committed, _source_id: uuid.UUID
) -> None:
    """Two processes resolving the same (venue, ticker) for the same instrument must not create two listings.

    Exchange and security are pre-seeded sequentially (committed, outside the
    race) so neither collides during the race itself; only
    ``_resolve_listing``'s own ``uq_listing_current_exchange_ticker`` path is
    exercised, by calling it directly with the pre-resolved ids -- the
    surgical equivalent of what ``resolve()`` would do once exchange and
    security are already settled.
    """
    seed_ref = _reference(RACE_TICKER_B, mic=RACE_MIC, cik=RACE_CIK, figi="BBG000RACESD")
    with committed() as session:
        seed = ReferenceResolver(session).resolve(seed_ref, source_id=_source_id, as_of=AS_OF)
        session.commit()
        exchange_id = session.scalar(sa.select(Exchange.id).where(Exchange.mic == RACE_MIC))
        security_id = seed.security_id
        assert exchange_id is not None

    target_ref = _reference(RACE_TICKER_A, mic=RACE_MIC, cik=RACE_CIK, figi="BBG000RACESD")

    row_inserted = threading.Event()
    winner_id: list[uuid.UUID] = []
    loser_id: list[uuid.UUID] = []

    def run_winner() -> None:
        with committed() as session:
            listing, _created = ReferenceResolver(session)._resolve_listing(
                target_ref, security_id=security_id, exchange_id=exchange_id, as_of=AS_OF
            )
            winner_id.append(listing.id)
            row_inserted.set()
            time.sleep(_HOLD_SECONDS)
            session.commit()

    def run_loser() -> None:
        assert row_inserted.wait(timeout=30), "winner never reached its insert"
        with committed() as session:
            listing, _created = ReferenceResolver(session)._resolve_listing(
                target_ref, security_id=security_id, exchange_id=exchange_id, as_of=AS_OF
            )
            loser_id.append(listing.id)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_winner), pool.submit(run_loser)]
        for future in futures:
            future.result(timeout=60)

    assert winner_id[0] == loser_id[0], "both callers must resolve to the same listing"
    with Session(committed().bind) as check:
        assert (
            check.scalar(
                sa.select(sa.func.count())
                .select_from(Listing)
                .where(Listing.ticker == RACE_TICKER_A)
            )
            == 1
        )


def test_concurrent_primary_listing_claims_on_two_venues_do_not_abort(
    committed: Committed, _source_id: uuid.UUID
) -> None:
    """Two venues racing to be a security's first (primary) listing must not abort.

    Unlike the identity races above, this is not about preventing a duplicate
    row -- ``(exchange_id, ticker)`` differs on each side, so both listings are
    legitimate. It is about ``uq_listing_current_primary_security``: with no
    listing yet for this security, ``_is_first_listing_for`` returns ``True``
    for *both* racers, and only one insert can actually claim ``is_primary``.
    The loser must retry non-primary inside its ``SAVEPOINT`` rather than
    raise a raw ``IntegrityError`` -- see the concurrency note on
    ``_resolve_listing``.
    """
    ref = _reference(RACE_TICKER_A, mic=RACE_MIC, cik=RACE_CIK, figi="BBG000RACEPR")
    with committed() as session:
        resolver = ReferenceResolver(session)
        exchange_a, _ = resolver._resolve_exchange(ref)
        company, _, _ = resolver._resolve_company(ref)
        security, _ = resolver._resolve_security(ref, company_id=company.id, source_id=_source_id)
        session.commit()
        exchange_a_id = exchange_a.id
        security_id = security.id

    other_ref = _reference(RACE_TICKER_A, mic=RACE_MIC_A, cik=RACE_CIK, figi="BBG000RACEPR")
    with committed() as session:
        exchange_b, _ = ReferenceResolver(session)._resolve_exchange(other_ref)
        session.commit()
        exchange_b_id = exchange_b.id

    row_inserted = threading.Event()
    primaries: list[bool] = []

    def run_winner() -> None:
        with committed() as session:
            listing, _created = ReferenceResolver(session)._resolve_listing(
                ref, security_id=security_id, exchange_id=exchange_a_id, as_of=AS_OF
            )
            row_inserted.set()
            time.sleep(_HOLD_SECONDS)
            session.commit()
            primaries.append(listing.is_primary)

    def run_loser() -> None:
        assert row_inserted.wait(timeout=30), "winner never reached its insert"
        with committed() as session:
            # Blocks here on uq_listing_current_primary_security until the
            # winner commits, then must retry non-primary rather than raise.
            listing, _created = ReferenceResolver(session)._resolve_listing(
                other_ref, security_id=security_id, exchange_id=exchange_b_id, as_of=AS_OF
            )
            session.commit()
            primaries.append(listing.is_primary)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_winner), pool.submit(run_loser)]
        for future in futures:
            future.result(timeout=60)

    assert sorted(primaries) == [False, True], "exactly one of the two listings claims primary"
    with Session(committed().bind) as check:
        assert (
            check.scalar(
                sa.select(sa.func.count())
                .select_from(Listing)
                .where(Listing.security_id == security_id, Listing.is_primary.is_(True))
            )
            == 1
        ), "the primary-listing constraint must still hold exactly once"


def test_concurrent_identical_full_reference_ingestion_converges_and_stays_idempotent(
    committed: Committed, _source_id: uuid.UUID
) -> None:
    """The full chain -- exchange, company, security, listing -- under real concurrency.

    Two callers resolve the *exact same* reference at once (every identity
    shared). The collision is inevitably forced at the earliest stage
    (exchange), and everything downstream -- company, security, listing --
    then resolves through the ordinary "already exists" path once the loser
    unblocks, because the winner has fully committed by then. That is still
    the property this test exists to pin: whichever interleaving occurs, both
    callers converge on the identical chain of ids with nothing duplicated
    anywhere in it, and a third, fully sequential resolution afterwards
    remains idempotent.
    """
    shared_figi = "BBG000RACEFL"
    ref = _reference(RACE_TICKER_A, mic=RACE_MIC, cik=RACE_CIK, figi=shared_figi)
    winner, loser = _race_resolve(
        winner_ref=ref, loser_ref=ref, committed=committed, source_id=_source_id
    )
    assert winner == loser, "both racers must converge on the identical chain of ids"

    with Session(committed().bind) as check:
        assert (
            check.scalar(
                sa.select(sa.func.count()).select_from(Company).where(Company.cik == RACE_CIK)
            )
            == 1
        )
        assert (
            check.scalar(
                sa.select(sa.func.count()).select_from(Exchange).where(Exchange.mic == RACE_MIC)
            )
            == 1
        )
        assert (
            check.scalar(
                sa.select(sa.func.count())
                .select_from(Listing)
                .where(Listing.ticker == RACE_TICKER_A)
            )
            == 1
        )

        third = ReferenceResolver(check).resolve(ref, source_id=_source_id, as_of=AS_OF)
        check.commit()
        assert third.created is False
        assert (third.company_id, third.security_id, third.listing_id) == winner
