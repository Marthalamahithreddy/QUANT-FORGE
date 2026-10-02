"""Registering a provider as a :class:`DataSource` before anything cites it.

Every observation row carries a mandatory ``source_id``, so ingestion cannot write
a single price until the provider exists as a row. This is the get-or-create that
makes that true.

Why the name is the key
-----------------------
``DataSource.name`` is unique, and the provider identity is already the one string
that means "this vendor" everywhere else in the system: it is
``IngestionRun.provider``, the raw store's first path segment, and the adapter's
``name`` property. Reusing it as the lookup key means there is exactly one
identity to keep stable, and changing a vendor's name is a deliberate,
searchable act rather than a silent re-pointing of provenance.

Concurrency
-----------
Two ingestion processes starting at once will both find no row and both try to
insert. The unique index on ``name`` is what actually settles it, and this handles
the resulting ``IntegrityError`` by re-reading the row the winner inserted. The
alternative -- ``SELECT`` then ``INSERT`` with a check -- loses that race and turns
a routine simultaneous start into a failed job.

Deliberately *not* ``INSERT ... ON CONFLICT DO NOTHING``, unlike the reference and
daily-bar writers elsewhere in this package: ``ON CONFLICT`` needs the same
snapshot guarantees a plain unique-constraint check does not, and under
``REPEATABLE READ`` (which a caller may set, and which
``test_a_lost_insert_reuses_the_winners_row`` deliberately does to make this race
deterministic) PostgreSQL raises it as a bare serialization failure rather than
silently reporting "no row inserted". A plain ``INSERT`` plus catching the unique
violation behaves the same way under every isolation level, which is what this
function is actually tested against.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quantforge.models.enums import ProviderType
from quantforge.models.provenance import DataSource

__all__ = ["get_or_create_data_source"]


def get_or_create_data_source(
    session: Session,
    *,
    name: str,
    provider_type: ProviderType = ProviderType.MARKET_DATA_VENDOR,
    base_url: str | None = None,
    is_active: bool = True,
) -> DataSource:
    """Return the source named ``name``, creating it if absent.

    An existing row is returned untouched. Its ``base_url`` is *not* updated to
    match the argument: a stored source is provenance, and silently rewriting
    where a vendor was reached from would rewrite history about it. Point a source
    at a new URL as an explicit change if that is what is wanted.

    Args:
        session: Caller's session. Nothing is committed.
        name: Stable provider identity, matching the adapter's ``name``.
        provider_type: Defaults to a commercial vendor, the right default for
            every market-data adapter.
        base_url: Recorded on creation only.
        is_active: Recorded on creation only.

    Returns:
        The existing or newly created row, with its id populated.
    """
    existing = session.scalar(select(DataSource).where(DataSource.name == name))
    if existing is not None:
        return existing

    created = DataSource(
        name=name,
        provider_type=provider_type,
        base_url=base_url,
        is_active=is_active,
    )
    session.add(created)
    try:
        # Flushed rather than left pending so the id is available to callers, and
        # so a concurrent insert surfaces here as a catchable IntegrityError
        # instead of at some later commit far from the cause.
        session.flush()
    except IntegrityError:
        session.rollback()
        # Lost the race: the other transaction's row is the one to use. If it is
        # somehow still absent, the constraint fired for a different reason and
        # re-raising is the honest outcome.
        raced = session.scalar(select(DataSource).where(DataSource.name == name))
        if raced is None:
            raise
        return raced
    return created
