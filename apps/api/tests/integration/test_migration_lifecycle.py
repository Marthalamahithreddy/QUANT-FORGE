"""Migration lifecycle test: upgrade head, verify, downgrade, re-upgrade.

The schema conformance tests exercise ingestion against a database brought to
``head`` automatically. What they do not prove is the *migration chain itself*:
that ``0001 -> ... -> 0006`` is one coherent scripted history, that ``0006`` is
really the head, that ``alembic check`` finds nothing pending against the
models, and -- most easily broken -- that the latest revisions can be downgraded
and then re-applied. A downgrade bug is invisible until an
operator needs one, which is exactly when it is cheapest to ship one.

This test uses its own scratch database so that downgrading it cannot disturb the
shared ``quantforge_test`` database the other integration tests run against.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]

SCRATCH_DB_NAME = "quantforge_migrations_test"
HEAD = "0009"
PRIOR = "0005"
#: Revision before 0005 -- the 0005 tests below downgrade past 0006 to reach it.
PRE_0005 = "0004"


def _scratch_url(admin_url: str) -> str:
    parts = urlsplit(admin_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{SCRATCH_DB_NAME}", "", ""))


def _alembic_config(url: str) -> Config:
    config = Config(API_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _admin_statements(admin_url: str, statements: list[str], **params: str) -> None:
    """Run maintenance SQL in autocommit mode, disposing the engine afterwards.

    ``CREATE/DROP DATABASE`` cannot run inside a transaction, and an undisposed
    engine would publish a ``ResourceWarning`` under the suite's
    ``filterwarnings = ["error"]``.
    """
    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.execute(sa.text(statement), params)
    finally:
        engine.dispose()


def _terminate_and_drop(admin_url: str) -> None:
    _admin_statements(
        admin_url,
        [
            (
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            # An identifier cannot be a bound parameter in DDL, and the name is
            # a module constant, so it is interpolated rather than substituted.
            f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}"',
        ],
        name=SCRATCH_DB_NAME,
    )


def _table_names(engine: Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _column_names(engine: Engine, table: str) -> set[str]:
    inspector = sa.inspect(engine)
    return {column["name"] for column in inspector.get_columns(table)}


def _current_version(engine: Engine) -> str | None:
    with engine.connect() as connection:
        version = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
    return version if isinstance(version, str) else None


@pytest.fixture(scope="module")
def scratch_database(postgres_admin_url: str) -> Iterator[Engine]:
    """Create, migrate, exercise, and drop a dedicated lifecycle database."""
    _terminate_and_drop(postgres_admin_url)
    _admin_statements(postgres_admin_url, [f'CREATE DATABASE "{SCRATCH_DB_NAME}"'])
    url = _scratch_url(postgres_admin_url)
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        _terminate_and_drop(postgres_admin_url)


def test_upgrade_to_head_produces_the_expected_chain(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    """The scripted history must be exactly the committed chain ending at head."""
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    assert _current_version(scratch_database) == HEAD

    scripts = ScriptDirectory.from_config(config)
    chain = [script.revision for script in scripts.walk_revisions()]
    assert chain == [HEAD, "0008", "0007", "0006", PRIOR, PRE_0005, "0003", "0002", "0001"], (
        f"unexpected chain {chain}"
    )
    assert scripts.get_current_head() == HEAD


def test_alembic_check_finds_nothing_pending_against_the_models(
    scratch_database: Engine,
) -> None:
    """``check`` compares autogenerate output; a drift here is a real finding."""
    config = _alembic_config(scratch_database.url.render_as_string(hide_password=False))

    # command.check logs a diff and raises when the models differ from the
    # schema, so reaching the line below without an exception is the assertion.
    command.check(config)


def test_the_latest_columns_exist_at_head(scratch_database: Engine) -> None:
    command.upgrade(
        _alembic_config(scratch_database.url.render_as_string(hide_password=False)), HEAD
    )
    assert "ingestion_run" in _table_names(scratch_database)
    assert {"vwap", "trades"} <= _column_names(scratch_database, "daily_price_bar")
    assert "cik" in _column_names(scratch_database, "company")
    # 0004: the earnings exact-observation key and the run link.
    assert {"fingerprint", "ingestion_run_id"} <= _column_names(scratch_database, "earnings_event")
    # 0005: the calendar date is now its own column, and event_timestamp is
    # nullable rather than required.
    assert "event_date" in _column_names(scratch_database, "earnings_event")
    inspector = sa.inspect(scratch_database)
    columns = {column["name"]: column for column in inspector.get_columns("earnings_event")}
    assert columns["event_date"]["nullable"] is False
    assert columns["event_timestamp"]["nullable"] is True


def test_existing_0004_rows_backfill_event_date_from_event_timestamp(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    """A row written before 0005 existed must get the right ``event_date``.

    Simulates the real scenario 0005's upgrade path has to handle: a
    pre-existing ``earnings_event`` row from when the table had no
    ``event_date`` column at all. Its ``event_timestamp`` is the only
    temporal fact available to derive one from, and the derivation must go
    through UTC explicitly -- an instant just after midnight UTC belongs to
    the *previous* UTC calendar day, which a naive session-timezone-dependent
    cast could get wrong.
    """
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.downgrade(config, PRE_0005)
    assert _current_version(scratch_database) == PRE_0005
    assert "event_date" not in _column_names(scratch_database, "earnings_event")

    company_id = uuid.uuid4()
    source_id = uuid.uuid4()
    # 23:30 UTC on the 14th -- deliberately not a boundary case, so the test
    # is not accidentally right for the wrong reason.
    stored_timestamp = datetime(2026, 9, 14, 23, 30, tzinfo=UTC)
    now = datetime.now(UTC)
    with scratch_database.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO company (id, legal_name, display_name, created_at, updated_at) "
                "VALUES (:id, 'Backfill Co', 'Backfill Co', :now, :now)"
            ),
            {"id": company_id, "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO data_source (id, name, provider_type, is_active, created_at) "
                "VALUES (:id, 'backfill-source', 'MARKET_DATA_VENDOR', true, :now)"
            ),
            {"id": source_id, "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO earnings_event "
                "(id, company_id, fiscal_year, fiscal_quarter, event_timestamp, timing, "
                "source_id, fingerprint, ingested_at, created_at, updated_at) "
                "VALUES (:id, :company_id, 2026, 3, :event_timestamp, 'UNKNOWN', "
                ":source_id, :fingerprint, :now, :now, :now)"
            ),
            {
                "id": uuid.uuid4(),
                "company_id": company_id,
                "event_timestamp": stored_timestamp,
                "source_id": source_id,
                "fingerprint": "x" * 64,
                "now": now,
            },
        )

    command.upgrade(config, HEAD)
    assert _current_version(scratch_database) == HEAD

    with scratch_database.connect() as connection:
        backfilled: date = connection.execute(
            sa.text("SELECT event_date FROM earnings_event WHERE company_id = :company_id"),
            {"company_id": company_id},
        ).scalar_one()
    assert backfilled == date(2026, 9, 14)

    # Clean up so this row does not leak into the downgrade test below.
    with scratch_database.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM earnings_event WHERE company_id = :company_id"),
            {"company_id": company_id},
        )
        connection.execute(sa.text("DELETE FROM company WHERE id = :id"), {"id": company_id})
        connection.execute(sa.text("DELETE FROM data_source WHERE id = :id"), {"id": source_id})


def test_downgrade_refuses_when_a_row_has_no_event_timestamp(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    """A date-only row cannot be downgraded without fabricating an instant.

    0005 exists specifically so a date-only observation does not need a
    manufactured ``event_timestamp``; downgrading past it must not turn
    around and manufacture one anyway just to satisfy the old NOT NULL
    constraint. It must refuse loudly instead.
    """
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    assert _current_version(scratch_database) == HEAD

    company_id = uuid.uuid4()
    source_id = uuid.uuid4()
    now = datetime.now(UTC)
    with scratch_database.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO company (id, legal_name, display_name, created_at, updated_at) "
                "VALUES (:id, 'Date Only Co', 'Date Only Co', :now, :now)"
            ),
            {"id": company_id, "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO data_source (id, name, provider_type, is_active, created_at) "
                "VALUES (:id, 'date-only-source', 'MARKET_DATA_VENDOR', true, :now)"
            ),
            {"id": source_id, "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO earnings_event "
                "(id, company_id, fiscal_year, event_date, event_timestamp, timing, "
                "source_id, fingerprint, ingested_at, created_at, updated_at) "
                "VALUES (:id, :company_id, 2026, :event_date, NULL, 'UNKNOWN', "
                ":source_id, :fingerprint, :now, :now, :now)"
            ),
            {
                "id": uuid.uuid4(),
                "company_id": company_id,
                "event_date": date(2026, 9, 20),
                "source_id": source_id,
                "fingerprint": "y" * 64,
                "now": now,
            },
        )

    try:
        with pytest.raises(Exception, match="event_timestamp"):
            command.downgrade(config, PRE_0005)
        # The refusal must not have left the schema half-migrated.
        assert _current_version(scratch_database) == HEAD
    finally:
        with scratch_database.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM earnings_event WHERE company_id = :company_id"),
                {"company_id": company_id},
            )
            connection.execute(sa.text("DELETE FROM company WHERE id = :id"), {"id": company_id})
            connection.execute(sa.text("DELETE FROM data_source WHERE id = :id"), {"id": source_id})


def test_downgrade_to_previous_revision_removes_the_new_schema(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    """A working ``downgrade`` is the only way to learn a migration is reversible."""
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.downgrade(config, PRE_0005)
    assert _current_version(scratch_database) == PRE_0005

    assert "ingestion_run" in _table_names(scratch_database)  # 0003 survives
    assert "vwap" in _column_names(scratch_database, "daily_price_bar")
    assert "cik" in _column_names(scratch_database, "company")
    # 0004's additions survive a downgrade of only one step (to 0004 itself).
    assert {"fingerprint", "ingestion_run_id"} <= _column_names(scratch_database, "earnings_event")
    # 0005's addition is gone; 0004's earnings_event did not have it.
    assert "event_date" not in _column_names(scratch_database, "earnings_event")


def test_reupgrade_restores_head_from_previous_revision(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    """The chain must be round-trippable: downgrade then upgrade leaves head."""
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    assert _current_version(scratch_database) == HEAD
    assert "ingestion_run" in _table_names(scratch_database)
    assert "cik" in _column_names(scratch_database, "company")
    assert {"fingerprint", "ingestion_run_id"} <= _column_names(scratch_database, "earnings_event")
    assert "event_date" in _column_names(scratch_database, "earnings_event")


# ── 0006: explicit price basis ──────────────────────────────────────────────

_SEED_ENTITIES = (
    (
        "INSERT INTO company (id, legal_name, display_name, created_at, updated_at) "
        "VALUES (:company_id, 'Basis Co', 'Basis Co', :now, :now)"
    ),
    (
        "INSERT INTO data_source (id, name, provider_type, is_active, created_at) "
        "VALUES (:source_id, 'basis-source', 'MARKET_DATA_VENDOR', true, :now)"
    ),
    (
        "INSERT INTO exchange (id, mic, name, timezone, is_active, created_at, updated_at) "
        "VALUES (:exchange_id, 'XBAS', 'Basis Exchange', 'America/New_York', true, :now, :now)"
    ),
    (
        "INSERT INTO security (id, company_id, security_type, name, currency, is_active, "
        "created_at, updated_at) VALUES (:security_id, :company_id, 'EQUITY', 'Shares', "
        "'USD', true, :now, :now)"
    ),
    (
        "INSERT INTO listing (id, security_id, exchange_id, ticker, valid_from, is_primary, "
        "created_at, updated_at) VALUES (:listing_id, :security_id, :exchange_id, 'BAS', "
        "'2018-01-02', true, :now, :now)"
    ),
    (
        "INSERT INTO benchmark (id, name, symbol, is_active, created_at, updated_at) "
        "VALUES (:benchmark_id, 'Basis Benchmark', 'BAS', true, :now, :now)"
    ),
)

_PRE_0006_BARS = (
    (
        "INSERT INTO daily_price_bar (id, listing_id, trading_date, open, high, low, close, "
        "source_id, ingested_at, created_at) VALUES (gen_random_uuid(), :listing_id, "
        "'2024-03-15', 100, 105, 99, 104, :source_id, :now, :now)"
    ),
    (
        "INSERT INTO benchmark_price_bar (id, benchmark_id, trading_date, close, source_id, "
        "ingested_at, created_at) VALUES (gen_random_uuid(), :benchmark_id, '2024-03-15', "
        "500, :source_id, :now, :now)"
    ),
)


def _seed_entities(engine: Engine, *, with_pre_0006_bars: bool = False) -> dict[str, uuid.UUID]:
    """Insert one listing and one benchmark (and optionally pre-0006 bars) via raw SQL."""
    ids = {
        name: uuid.uuid4()
        for name in ("company", "security", "exchange", "listing", "source", "benchmark")
    }
    params = {**{f"{key}_id": value for key, value in ids.items()}, "now": datetime.now(UTC)}
    statements = (*_SEED_ENTITIES, *(_PRE_0006_BARS if with_pre_0006_bars else ()))
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(sa.text(statement), params)
    return ids


def _purge_seed(engine: Engine, ids: dict[str, uuid.UUID]) -> None:
    with engine.begin() as connection:
        for statement in (
            "DELETE FROM daily_price_bar WHERE listing_id = :listing",
            "DELETE FROM benchmark_price_bar WHERE benchmark_id = :benchmark",
            "DELETE FROM benchmark WHERE id = :benchmark",
            "DELETE FROM listing WHERE id = :listing",
            "DELETE FROM security WHERE id = :security",
            "DELETE FROM exchange WHERE id = :exchange",
            "DELETE FROM company WHERE id = :company",
            "DELETE FROM data_source WHERE id = :source",
        ):
            connection.execute(sa.text(statement), ids)


def _insert_daily_bar(
    engine: Engine, ids: dict[str, uuid.UUID], basis: str | None, *, close: int = 104
) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO daily_price_bar (id, listing_id, trading_date, open, high, low, "
                "close, source_id, price_basis, ingested_at, created_at) VALUES "
                "(gen_random_uuid(), :listing, '2024-03-15', 100, 2000, 1, :close, :source, "
                ":basis, now(), now())"
            ),
            {**ids, "basis": basis, "close": close},
        )


def _insert_benchmark_level(
    engine: Engine, ids: dict[str, uuid.UUID], basis: str, *, close: int = 500
) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO benchmark_price_bar (id, benchmark_id, trading_date, close, "
                "source_id, price_basis, ingested_at, created_at) VALUES "
                "(gen_random_uuid(), :benchmark, '2024-03-15', :close, :source, :basis, "
                "now(), now())"
            ),
            {**ids, "basis": basis, "close": close},
        )


def test_0006_backfills_existing_bars_as_split_adjusted(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    """Every pre-0006 row (both tables) is labelled SPLIT_ADJUSTED, and the column is NOT NULL."""
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    command.downgrade(config, PRIOR)
    assert _current_version(scratch_database) == PRIOR
    assert "price_basis" not in _column_names(scratch_database, "daily_price_bar")
    ids = _seed_entities(scratch_database, with_pre_0006_bars=True)
    try:
        command.upgrade(config, HEAD)

        with scratch_database.connect() as connection:
            daily: list[object] = list(
                connection.execute(
                    sa.text("SELECT price_basis FROM daily_price_bar WHERE listing_id = :listing"),
                    ids,
                ).scalars()
            )
            bench: list[object] = list(
                connection.execute(
                    sa.text(
                        "SELECT price_basis FROM benchmark_price_bar "
                        "WHERE benchmark_id = :benchmark"
                    ),
                    ids,
                ).scalars()
            )
        assert daily == ["SPLIT_ADJUSTED"]
        assert bench == ["SPLIT_ADJUSTED"]

        inspector = sa.inspect(scratch_database)
        for table in ("daily_price_bar", "benchmark_price_bar"):
            column = {c["name"]: c for c in inspector.get_columns(table)}["price_basis"]
            assert column["nullable"] is False
            assert column["default"] is None, "the database must never invent a basis"
    finally:
        _purge_seed(scratch_database, ids)


def test_0006_constraints_enforce_basis_and_allow_two_bases_per_session(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    command.upgrade(_alembic_config(_scratch_url(postgres_admin_url)), HEAD)
    ids = _seed_entities(scratch_database)
    try:
        # Two bases for one listing/date/source coexist...
        _insert_daily_bar(scratch_database, ids, "SPLIT_ADJUSTED")
        _insert_daily_bar(scratch_database, ids, "AS_TRADED", close=1040)
        # ...a duplicate of the same basis does not...
        with pytest.raises(sa.exc.IntegrityError, match="uq_daily_price_bar_listing_date"):
            _insert_daily_bar(scratch_database, ids, "AS_TRADED", close=999)
        # ...an unknown basis does not...
        with pytest.raises(sa.exc.IntegrityError, match="price_basis_enum_values"):
            _insert_daily_bar(scratch_database, ids, "RAW")
        # ...and NULL does not.
        with pytest.raises(sa.exc.IntegrityError, match="price_basis"):
            _insert_daily_bar(scratch_database, ids, None)

        # The benchmark table behaves identically.
        _insert_benchmark_level(scratch_database, ids, "SPLIT_ADJUSTED")
        _insert_benchmark_level(scratch_database, ids, "AS_TRADED", close=5000)
        with pytest.raises(sa.exc.IntegrityError, match="uq_benchmark_price_bar_benchmark_date"):
            _insert_benchmark_level(scratch_database, ids, "SPLIT_ADJUSTED", close=1)
        with pytest.raises(sa.exc.IntegrityError, match="price_basis_enum_values"):
            _insert_benchmark_level(scratch_database, ids, "RAW")
    finally:
        _purge_seed(scratch_database, ids)


def test_0006_downgrade_refuses_while_as_traded_rows_exist_then_restores_old_keys(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    ids = _seed_entities(scratch_database)
    try:
        _insert_daily_bar(scratch_database, ids, "AS_TRADED")
        with pytest.raises(Exception, match="AS_TRADED"):
            command.downgrade(config, PRIOR)
        assert _current_version(scratch_database) == HEAD

        with scratch_database.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM daily_price_bar WHERE listing_id = :listing"), ids
            )
        command.downgrade(config, PRIOR)
        assert _current_version(scratch_database) == PRIOR
        assert "price_basis" not in _column_names(scratch_database, "daily_price_bar")
        assert "price_basis" not in _column_names(scratch_database, "benchmark_price_bar")
        inspector = sa.inspect(scratch_database)
        for table, owner in (
            ("daily_price_bar", "listing_id"),
            ("benchmark_price_bar", "benchmark_id"),
        ):
            uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)}
            assert (owner, "trading_date", "source_id") in uniques
    finally:
        command.upgrade(config, HEAD)
        _purge_seed(scratch_database, ids)


# ── 0007: availability basis; 0008: SEC filing metadata ─────────────────────


def _insert_earnings(
    engine: Engine, ids: dict[str, uuid.UUID], *, basis_sql: str, observed_sql: str, tag: str
) -> None:
    columns = "observed_at_basis, " if basis_sql != "OMIT" else ""
    values = f"{basis_sql}, " if basis_sql != "OMIT" else ""
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO earnings_event (id, company_id, fiscal_year, event_date, timing, "
                f"source_id, fingerprint, observed_at, {columns}ingested_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :company, 2024, '2024-03-15', 'UNKNOWN', :source, "
                f":fingerprint, {observed_sql}, {values}now(), now(), now())"
            ),
            {**ids, "fingerprint": tag.ljust(64, "0")},
        )


def test_0007_refuses_to_guess_a_basis_for_an_existing_timestamp(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    command.downgrade(config, "0006")
    assert "observed_at_basis" not in _column_names(scratch_database, "earnings_event")
    ids = _seed_entities(scratch_database)
    try:
        _insert_earnings(scratch_database, ids, basis_sql="OMIT", observed_sql="now()", tag="a")

        with pytest.raises(Exception, match="already have an observed_at"):
            command.upgrade(config, "0007")
        assert _current_version(scratch_database) == "0006"

        with scratch_database.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM earnings_event WHERE company_id = :company"), ids
            )
        _insert_earnings(scratch_database, ids, basis_sql="OMIT", observed_sql="NULL", tag="b")
        command.upgrade(config, HEAD)  # NULL timestamps need no basis: the upgrade is safe

        with scratch_database.connect() as connection:
            basis: list[object] = list(
                connection.execute(
                    sa.text(
                        "SELECT observed_at_basis FROM earnings_event WHERE company_id = :company"
                    ),
                    ids,
                ).scalars()
            )
        assert basis == [None], "existing rows keep NULL/NULL"
    finally:
        with scratch_database.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM earnings_event WHERE company_id = :company"), ids
            )
        _purge_seed(scratch_database, ids)
        command.upgrade(config, HEAD)


def test_0007_constraints_enforce_the_timestamp_basis_pairing(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    command.upgrade(_alembic_config(_scratch_url(postgres_admin_url)), HEAD)
    ids = _seed_entities(scratch_database)
    try:
        _insert_earnings(scratch_database, ids, basis_sql="NULL", observed_sql="NULL", tag="c")
        _insert_earnings(
            scratch_database, ids, basis_sql="'SOURCE_PUBLICATION'", observed_sql="now()", tag="d"
        )
        _insert_earnings(
            scratch_database,
            ids,
            basis_sql="'SEC_FILING_ACCEPTANCE'",
            observed_sql="now()",
            tag="e",
        )
        with pytest.raises(sa.exc.IntegrityError, match="observed_at_basis_pairing"):
            _insert_earnings(scratch_database, ids, basis_sql="NULL", observed_sql="now()", tag="f")
        with pytest.raises(sa.exc.IntegrityError, match="observed_at_basis_pairing"):
            _insert_earnings(
                scratch_database,
                ids,
                basis_sql="'SOURCE_PUBLICATION'",
                observed_sql="NULL",
                tag="g",
            )
        with pytest.raises(sa.exc.IntegrityError, match="observed_at_basis_enum_values"):
            _insert_earnings(
                scratch_database, ids, basis_sql="'FIRST_PUBLIC'", observed_sql="now()", tag="h"
            )
    finally:
        with scratch_database.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM earnings_event WHERE company_id = :company"), ids
            )
        _purge_seed(scratch_database, ids)


def test_0007_downgrade_refuses_while_a_row_carries_a_basis(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    ids = _seed_entities(scratch_database)
    try:
        _insert_earnings(
            scratch_database, ids, basis_sql="'SOURCE_PUBLICATION'", observed_sql="now()", tag="i"
        )
        with pytest.raises(Exception, match="carry an observed_at_basis"):
            command.downgrade(config, "0006")
        assert _current_version(scratch_database) == HEAD

        with scratch_database.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM earnings_event WHERE company_id = :company"), ids
            )
        command.downgrade(config, "0006")
        assert "observed_at_basis" not in _column_names(scratch_database, "earnings_event")
    finally:
        _purge_seed(scratch_database, ids)
        command.upgrade(config, HEAD)


def test_0008_creates_sec_filing_and_its_downgrade_refuses_while_rows_exist(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    assert {"accession_number", "accepted_at", "filing_date", "company_id"} <= _column_names(
        scratch_database, "sec_filing"
    )
    ids = _seed_entities(scratch_database)
    try:
        with scratch_database.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO sec_filing (id, company_id, accession_number, form, filing_date, "
                    "accepted_at, source_id, ingested_at, created_at) VALUES (gen_random_uuid(), "
                    ":company, '0000320193-24-000010', '10-Q', '2024-05-03', now(), :source, "
                    "now(), now())"
                ),
                ids,
            )
        with pytest.raises(Exception, match="sec_filing row"):
            command.downgrade(config, "0007")
        assert _current_version(scratch_database) == HEAD

        with scratch_database.begin() as connection:
            connection.execute(sa.text("DELETE FROM sec_filing WHERE company_id = :company"), ids)
        command.downgrade(config, "0007")
        assert "sec_filing" not in _table_names(scratch_database)
    finally:
        _purge_seed(scratch_database, ids)
        command.upgrade(config, HEAD)


def test_0009_creates_split_tables_and_its_downgrade_refuses_while_rows_exist(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    config = _alembic_config(_scratch_url(postgres_admin_url))
    command.upgrade(config, HEAD)
    assert {"security_id", "execution_date", "old_shares", "new_shares"} <= _column_names(
        scratch_database, "stock_split"
    )
    assert {"security_id", "covered_from", "covered_to"} <= _column_names(
        scratch_database, "stock_split_coverage"
    )
    ids = _seed_entities(scratch_database)
    try:
        with scratch_database.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO stock_split_coverage (id, security_id, source_id, "
                    "provider_ticker, covered_from, covered_to, created_at) VALUES "
                    "(gen_random_uuid(), :security, :source, 'T', '2024-01-01', '2024-02-01', now())"
                ),
                ids,
            )
        with pytest.raises(Exception, match="stock_split_coverage row"):
            command.downgrade(config, "0008")
        assert _current_version(scratch_database) == HEAD

        with scratch_database.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM stock_split_coverage WHERE security_id = :security"), ids
            )
        command.downgrade(config, "0008")
        assert "stock_split" not in _table_names(scratch_database)
        assert "stock_split_coverage" not in _table_names(scratch_database)
    finally:
        _purge_seed(scratch_database, ids)
        command.upgrade(config, HEAD)


def test_0009_constraints_reject_non_positive_ratios_and_inverted_coverage(
    postgres_admin_url: str, scratch_database: Engine
) -> None:
    command.upgrade(_alembic_config(_scratch_url(postgres_admin_url)), HEAD)
    ids = _seed_entities(scratch_database)
    try:
        for old, new in ((0, 2), (1, 0), (-1, 2)):
            with pytest.raises(sa.exc.IntegrityError), scratch_database.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO stock_split (id, security_id, source_id, provider_split_id, "
                        "provider_ticker, execution_date, old_shares, new_shares, "
                        "adjustment_type, ingested_at, created_at) VALUES (gen_random_uuid(), "
                        ":security, :source, 'x', 'T', '2024-06-10', :old, :new, "
                        "'FORWARD_SPLIT', now(), now())"
                    ),
                    {**ids, "old": old, "new": new},
                )
        with pytest.raises(sa.exc.IntegrityError), scratch_database.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO stock_split_coverage (id, security_id, source_id, "
                    "provider_ticker, covered_from, covered_to, created_at) VALUES "
                    "(gen_random_uuid(), :security, :source, 'T', '2024-02-01', '2024-01-01', now())"
                ),
                ids,
            )
    finally:
        _purge_seed(scratch_database, ids)
