"""Integration tests for DataSource creation, against real PostgreSQL.

Getting a DataSource wrong is quiet: a duplicate row means two "massive" sources,
prices get attributed to whichever the query happened to join, and a provenance
question becomes unanswerable. The uniqueness constraint and the get-or-create
race are therefore worth pinning down against the real database rather than
in-memory.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.enums import ProviderType
from quantforge.models.provenance import DataSource

pytestmark = pytest.mark.integration

#: A name no other test uses, so the tests that must commit for real cannot
#: collide with the savepoint-rolled-back rows the rest of the suite creates.
RACE_NAME = "massive-race-probe"

#: Alias for the ``committed`` fixture's factory, so tests can annotate it.
Committed = Callable[..., Session]


def _providers(session: Session) -> list[str]:
    return sorted(session.scalars(sa.select(DataSource.name)))


@pytest.fixture
def committed(integration_engine: Engine) -> Iterator[Callable[..., Session]]:
    """Yield a factory for sessions that really commit, and clean up after.

    Most integration tests here use ``db_session``, which rolls everything back.
    A concurrency test cannot: two sessions have to see each other's writes, which
    means real commits, which means the rows outlive the test unless removed.
    """
    made: list[str] = []

    def factory(name: str | None = None) -> Session:
        if name is not None:
            made.append(name)
        return Session(integration_engine)

    yield factory

    with Session(integration_engine) as cleanup:
        for name in made:
            cleanup.execute(sa.delete(DataSource).where(DataSource.name == name))
        cleanup.commit()


def test_a_new_source_is_created(db_session: Session) -> None:
    source = get_or_create_data_source(db_session, name="massive")

    assert source.id is not None, "the row was flushed, so the id is real"
    assert source.name == "massive"
    assert source.is_active is True
    assert _providers(db_session) == ["massive"]


def test_asking_again_returns_the_same_row(db_session: Session) -> None:
    first = get_or_create_data_source(db_session, name="massive")
    db_session.commit()

    second = get_or_create_data_source(db_session, name="massive")
    db_session.commit()

    assert second.id == first.id
    assert db_session.query(DataSource).count() == 1


def test_repeated_lookups_never_duplicate(db_session: Session) -> None:
    ids = {get_or_create_data_source(db_session, name="massive").id for _ in range(5)}
    db_session.commit()

    assert len(ids) == 1
    assert db_session.query(DataSource).count() == 1


def test_different_providers_coexist(db_session: Session) -> None:
    get_or_create_data_source(db_session, name="massive")
    get_or_create_data_source(db_session, name="polygon")
    db_session.commit()

    assert _providers(db_session) == ["massive", "polygon"]


def test_an_existing_row_is_returned_untouched(db_session: Session) -> None:
    """Re-ingesting must not reset a source's configuration.

    The failure this prevents is quiet and expensive: a run that repopulates
    ``base_url`` from a default would silently repoint every future ingestion away
    from the endpoint an operator deliberately set.
    """
    row = get_or_create_data_source(db_session, name="massive", base_url="https://one")
    db_session.commit()
    row.base_url = "https://api.massive.example/v2"
    row.is_active = False
    db_session.commit()

    fetched = get_or_create_data_source(db_session, name="massive", base_url="https://two")
    db_session.commit()

    assert fetched.id == row.id
    assert fetched.base_url == "https://api.massive.example/v2"
    assert fetched.is_active is False, "an inactive source is not silently reactivated"


def test_the_database_rejects_a_duplicate_name(db_session: Session) -> None:
    """The guard behind the get-or-create is a constraint, not just our code.

    If this stops failing, something is inserting sources directly and the
    get-or-create is no longer the only writer.
    """
    db_session.add(DataSource(name="massive", provider_type=ProviderType.MARKET_DATA_VENDOR))
    db_session.flush()

    db_session.add(DataSource(name="massive", provider_type=ProviderType.MARKET_DATA_VENDOR))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def _stale_first_read(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the session's first ``scalar`` report "no such row", as a lost race would.

    Under the production isolation level (READ COMMITTED) the loser of the race
    reads *before* the winner commits, so its initial lookup sees nothing even
    though the row exists by the time it inserts. Reproduced deterministically
    by answering that first lookup with ``None``; every later read is real.
    """
    real = session.scalar
    calls = {"n": 0}

    def scalar(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*args, **kwargs)

    monkeypatch.setattr(session, "scalar", scalar)


def test_a_lost_insert_reuses_the_winners_row(
    committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loser of an insert race reuses the winner's row instead of failing."""
    winner = committed(RACE_NAME)
    loser = committed()

    created = get_or_create_data_source(winner, name=RACE_NAME)
    winner.commit()

    _stale_first_read(loser, monkeypatch)
    reused = get_or_create_data_source(loser, name=RACE_NAME)
    loser.commit()

    assert reused.id == created.id
    assert reused is not created

    with Session(committed().bind) as check:
        assert (
            check.scalar(
                sa.select(sa.func.count())
                .select_from(DataSource)
                .where(DataSource.name == RACE_NAME)
            )
            == 1
        )

    winner.close()
    loser.close()


def test_a_lost_insert_does_not_discard_the_callers_other_work(
    committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rolls back only its own SAVEPOINT: pending work in the same transaction survives."""
    winner = committed(RACE_NAME)
    loser = committed()
    marker = "pending-neighbour-source"
    committed(marker)

    get_or_create_data_source(winner, name=RACE_NAME)
    winner.commit()

    neighbour = DataSource(name=marker, provider_type=ProviderType.MARKET_DATA_VENDOR)
    loser.add(neighbour)
    loser.flush()  # flushed, uncommitted: exactly what a run row looks like mid-block
    _stale_first_read(loser, monkeypatch)
    get_or_create_data_source(loser, name=RACE_NAME)
    loser.commit()

    with Session(committed().bind) as check:
        names = set(
            check.scalars(
                sa.select(DataSource.name).where(DataSource.name.in_([RACE_NAME, marker]))
            )
        )
    assert names == {RACE_NAME, marker}, "the flushed neighbour row must survive the recovery"
    winner.close()
    loser.close()


@pytest.mark.anyio
async def test_simultaneous_callers_converge_on_one_row(committed: Committed) -> None:
    """Two processes starting at once must not create two sources.

    Whichever interleaving occurs, both callers end up citing the same row, so
    every price written by either is attributable to the same source.
    """
    barrier = threading.Barrier(2)

    def attempt() -> uuid.UUID:
        with committed(RACE_NAME) as session:
            session.scalar(sa.select(DataSource).where(DataSource.name == RACE_NAME))
            # Both have read before either writes, which is the window a
            # check-then-insert loses.
            barrier.wait(timeout=30)
            row = get_or_create_data_source(session, name=RACE_NAME)
            session.commit()
            return row.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        ids = [future.result(timeout=60) for future in futures]

    assert ids[0] == ids[1]
    with Session(committed().bind) as check:
        stored = check.scalar(sa.select(DataSource).where(DataSource.name == RACE_NAME))
        assert stored is not None
        assert stored.id == ids[0]
