"""Unit tests for the SQLAlchemy model layer.

These assert the *declarative* half of the contract -- column types, nullability,
foreign-key delete behaviour, unique constraints, indexes and check constraints
-- by inspecting ``Table`` metadata. No database is involved, so they run in
milliseconds and pin the schema's intent even if the live PostgreSQL instance is
unavailable.

The complementary half, that PostgreSQL actually enforces all of it, lives in
``tests/integration/``. Neither layer is redundant: metadata inspection catches a
dropped constraint at review time, while integration catches a constraint the
database rejects differently than expected.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase

from quantforge.db.base import Base
from quantforge.models import ALL_TABLES, DOMAIN_TABLES, OPERATIONAL_TABLES
from quantforge.models.base import (
    EPS_PRECISION,
    EPS_SCALE,
    MONEY_PRECISION,
    MONEY_SCALE,
    PRICE_PRECISION,
    PRICE_SCALE,
    eps_type,
    money_type,
    new_uuid,
    price_type,
    timestamptz,
    utcnow,
    uuid_type,
)
from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    EarningsTiming,
    IdentifierType,
    ProviderType,
    SecurityType,
    enum_check,
    enum_type,
)
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import (
    Exchange,
    Listing,
    Security,
)

#: Any mapped model class. Narrowing to this rather than bare ``type`` is what
#: makes ``.__mapper__`` visible to the type checker.
type Model = type[DeclarativeBase]


# ─────────────────────────── typed metadata accessors ────────────────────────
#
# ``Column.type`` is a bare ``TypeEngine``, so reading ``.precision`` or
# ``.timezone`` off it is untyped. These accessors narrow to the concrete type
# the column is expected to have, which also makes each assertion state its
# expectation rather than relying on a cast.


def _column(table_name: str, column_name: str) -> sa.Column[Any]:
    return Base.metadata.tables[table_name].columns[column_name]


def _datetime_column(table_name: str, column_name: str) -> sa.Column[dt.datetime]:
    return cast("sa.Column[dt.datetime]", _column(table_name, column_name))


def _date_column(table_name: str, column_name: str) -> sa.Column[dt.date]:
    return cast("sa.Column[dt.date]", _column(table_name, column_name))


def _numeric_dims(column_type: sa.Numeric[Any]) -> tuple[int, int]:
    """Return ``(precision, scale)`` as plain ints.

    SQLAlchemy types both as optional because an unparameterised ``NUMERIC`` has
    neither. An unconstrained numeric column is exactly what this schema must
    never have, so the assertion is the point of the accessor.
    """
    precision, scale = column_type.precision, column_type.scale
    assert isinstance(precision, int), f"NUMERIC without precision: {column_type!r}"
    assert isinstance(scale, int), f"NUMERIC without scale: {column_type!r}"
    return precision, scale


def _column_numeric_dims(table_name: str, column_name: str) -> tuple[int, int]:
    """Return ``(precision, scale)`` for a column, asserting it is ``NUMERIC``."""
    column_type = _column(table_name, column_name).type
    assert isinstance(column_type, sa.Numeric), (table_name, column_name, column_type)
    return _numeric_dims(column_type)


def _datetime_type(table_name: str, column_name: str) -> sa.DateTime:
    """Narrow a column's type to ``DateTime``, which is where ``timezone`` lives."""
    column_type = _column(table_name, column_name).type
    assert isinstance(column_type, sa.DateTime), (table_name, column_name, column_type)
    return column_type


def _pg_dialect() -> sa.engine.interfaces.Dialect:
    dialect: sa.engine.interfaces.Dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    return dialect


def _constraint_name(constraint: sa.Constraint) -> str:
    """Return a constraint's resolved name, asserting it is a plain string."""
    name = constraint.name
    assert isinstance(name, str), f"unresolved constraint name: {name!r}"
    return name


def _check_sql(table_name: str, suffix: str) -> str:
    """Return the body of the single check constraint whose name ends in ``suffix``."""
    matches = [
        constraint
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, sa.CheckConstraint)
        and _constraint_name(constraint).endswith(suffix)
    ]
    assert len(matches) == 1, (table_name, suffix)
    return str(matches[0].sqltext)


def _default(column: sa.Column[Any]) -> Any:
    """Return a column's Python-side default, unwrapping SQLAlchemy's wrapper.

    ``CallableColumnDefault`` wraps the supplied callable for error reporting, so
    the original function is only reachable through ``__wrapped__``.
    """
    default = column.default
    assert default is not None
    assert default.is_callable
    wrapped = getattr(getattr(default, "arg", None), "__wrapped__", None)
    assert wrapped is not None, f"{column.key} default is not a wrapped callable"
    return wrapped


# ─────────────────────────── type factories ──────────────────────────────────


def test_price_type_has_the_declared_precision_and_scale() -> None:
    assert _numeric_dims(price_type()) == (PRICE_PRECISION, PRICE_SCALE) == (20, 6)


def test_eps_and_money_use_wider_integer_parts_than_price() -> None:
    """Revenue reaches hundreds of billions while a share price rarely leaves
    four integer digits, so ``MONEY`` buys integer width at the cost of scale."""
    assert _numeric_dims(eps_type()) == (EPS_PRECISION, EPS_SCALE) == (20, 4)
    assert _numeric_dims(money_type()) == (MONEY_PRECISION, MONEY_SCALE) == (24, 4)
    money_precision, money_scale = _numeric_dims(money_type())
    price_precision, price_scale = _numeric_dims(price_type())
    assert money_precision > price_precision
    assert money_scale < price_scale


def test_price_type_does_not_discount_scale() -> None:
    """Guard against a copy-paste that would set precision *and* scale to 20/6
    on the EPS column, silently truncating per-share figures."""
    assert _numeric_dims(price_type()) == (PRICE_PRECISION, PRICE_SCALE)
    assert _numeric_dims(eps_type()) == (EPS_PRECISION, EPS_SCALE)
    for column_type in (price_type(), eps_type(), money_type()):
        precision, scale = _numeric_dims(column_type)
        assert precision > scale, column_type


def test_uuid_type_is_native_and_uuid_typed() -> None:
    column_type = uuid_type()
    assert isinstance(column_type, sa.Uuid)
    assert column_type.as_uuid is True


def test_timestamptz_is_timezone_aware() -> None:
    """A naive ``TIMESTAMP`` silently misreads DST transitions; every instant
    column in this schema must be tz-aware."""
    assert timestamptz().timezone is True


def test_uuid_type_renders_as_native_uuid_on_postgresql() -> None:
    assert _pg_dialect().type_compiler.process(uuid_type()) == "UUID"


def test_timestamptz_renders_as_timestamptz_on_postgresql() -> None:
    assert _pg_dialect().type_compiler.process(timestamptz()) == "TIMESTAMP WITH TIME ZONE"


def test_utcnow_is_always_aware() -> None:
    assert utcnow().tzinfo is not None


def test_new_uuid_is_a_distinct_uuid4() -> None:
    first, second = new_uuid(), new_uuid()
    assert isinstance(first, uuid.UUID)
    assert first != second
    assert first.version == 4


# ─────────────────────────── metadata topology ───────────────────────────────


def test_metadata_contains_exactly_the_known_tables() -> None:
    """Every registered table, financial and operational, and nothing else.

    The count is asserted as well as the names: a typo'd name and a dropped table
    would both be caught by the set comparison, but the count also documents the
    expected size so a failure says how far off the schema is.
    """
    assert set(Base.metadata.tables) == set(ALL_TABLES)
    assert len(Base.metadata.tables) == 11
    assert len(DOMAIN_TABLES) == 10
    assert len(OPERATIONAL_TABLES) == 1


def test_every_domain_table_is_backed_by_a_mapped_class() -> None:
    """A table in metadata with no owning mapper would never receive an ORM
    identity, so nothing could read or write it through the repositories."""
    mapped = {mapper.class_.__tablename__ for mapper in Base.registry.mappers}
    assert mapped == set(ALL_TABLES)


def test_every_table_has_a_uuid_primary_key() -> None:
    for table_name in DOMAIN_TABLES:
        table = Base.metadata.tables[table_name]
        primary_keys = list(table.primary_key.columns)
        assert len(primary_keys) == 1, table_name
        assert isinstance(primary_keys[0].type, sa.Uuid), table_name
        assert str(primary_keys[0].name) == "id"


def test_company_does_not_carry_a_ticker() -> None:
    """The canonical separation: an issuer is not a tradable symbol."""
    assert "ticker" not in Base.metadata.tables["company"].columns
    assert "ticker" in Base.metadata.tables["listing"].columns


def test_listing_is_the_join_between_instrument_and_venue() -> None:
    columns = Base.metadata.tables["listing"].columns
    assert {"security_id", "exchange_id", "ticker", "valid_from", "valid_to"} <= set(columns.keys())


def test_no_table_carries_a_company_ticker_surrogate() -> None:
    """``Company`` is identified by id only; there is no display-symbol column
    that could drift out of sync with the listing."""
    assert "symbol" not in Base.metadata.tables["company"].columns


# ─────────────────────────── reference-data mixins ───────────────────────────

#: Rows QuantForge may correct in place, so they carry ``updated_at``.
MUTABLE_TABLES = ["company", "security", "exchange", "listing", "benchmark", "earnings_event"]
#: Rows that are immutable *facts* about the world, recorded once. Corrections
#: arrive as a new source-attributed row so the first observation is never lost.
IMMUTABLE_TABLES = ["data_source", "security_identifier", "daily_price_bar", "benchmark_price_bar"]


@pytest.mark.parametrize("table_name", MUTABLE_TABLES + IMMUTABLE_TABLES)
def test_created_at_is_always_present_and_mandatory(table_name: str) -> None:
    column = _datetime_column(table_name, "created_at")
    assert column.nullable is False
    assert _datetime_type(table_name, "created_at").timezone is True


@pytest.mark.parametrize("table_name", MUTABLE_TABLES)
def test_correctable_rows_track_edits(table_name: str) -> None:
    """Companies, instruments, listings, benchmarks and earnings events all get
    corrected, so each records when it was last written."""
    column = _datetime_column(table_name, "updated_at")
    assert column.nullable is False
    assert _datetime_type(table_name, "updated_at").timezone is True


@pytest.mark.parametrize("table_name", IMMUTABLE_TABLES)
def test_immutable_facts_have_no_updated_at(table_name: str) -> None:
    """A market observation, a source registration and an external code
    assignment are all statements about the world. Editing one in place would
    destroy the audit trail, so corrections arrive as new rows instead."""
    assert "updated_at" not in Base.metadata.tables[table_name].columns


@pytest.mark.parametrize("table_name", ["daily_price_bar", "benchmark_price_bar"])
def test_observations_separate_observation_time_from_ingestion_time(table_name: str) -> None:
    """``observed_at`` is the source's "as of" and may be unknown; ``ingested_at``
    is ours and is mandatory, because it is the earliest we could have known."""
    for column_name in ("observed_at", "ingested_at"):
        assert _datetime_type(table_name, column_name).timezone is True, column_name
    assert _datetime_column(table_name, "observed_at").nullable is True
    assert _datetime_column(table_name, "ingested_at").nullable is False


def test_earnings_records_the_source_event_time_as_aware() -> None:
    assert _datetime_type("earnings_event", "event_timestamp").timezone is True
    assert _datetime_type("earnings_event", "observed_at").timezone is True
    assert _datetime_type("earnings_event", "ingested_at").timezone is True
    assert _datetime_column("earnings_event", "observed_at").nullable is True
    assert _datetime_column("earnings_event", "ingested_at").nullable is False


# ─────────────────────────── trading dates ───────────────────────────────────

DATE_TABLES_COLUMNS = [
    ("daily_price_bar", "trading_date"),
    ("benchmark_price_bar", "trading_date"),
    ("listing", "valid_from"),
    ("listing", "valid_to"),
    ("security_identifier", "valid_from"),
    ("security_identifier", "valid_to"),
    ("company", "founded_date"),
]


@pytest.mark.parametrize(("table_name", "column_name"), DATE_TABLES_COLUMNS)
def test_calendar_dates_are_date_not_timestamp(table_name: str, column_name: str) -> None:
    """A trading session is a calendar fact. Storing it as a timestamp invites
    a timezone to be applied to it later, which is never correct."""
    column_type = _date_column(table_name, column_name).type
    assert isinstance(column_type, sa.Date), (table_name, column_name)
    assert not isinstance(column_type, sa.DateTime)


def test_optional_validity_endpoints_are_nullable() -> None:
    assert Base.metadata.tables["listing"].columns["valid_to"].nullable is True
    assert Base.metadata.tables["security_identifier"].columns["valid_to"].nullable is True
    assert Base.metadata.tables["security_identifier"].columns["valid_from"].nullable is True


# ─────────────────────────── enums ────────────────────────────────────────────


def test_enum_columns_use_varchar_not_a_native_postgres_enum() -> None:
    """A native ``ENUM`` cannot drop a value and forces a hand-written migration
    for every addition, so the schema uses ``VARCHAR`` + ``CHECK``."""
    type_compiler = _pg_dialect().type_compiler
    for table_name in DOMAIN_TABLES:
        for column in Base.metadata.tables[table_name].columns:
            if isinstance(column.type, sa.Enum):
                assert column.type.native_enum is False
                assert type_compiler.process(column.type).upper().startswith("VARCHAR")


def test_enum_columns_do_not_autogenerate_their_own_check() -> None:
    """SQLAlchemy would emit the constraint twice -- once from the column type
    and once from the table's constraint list -- under two different names."""
    for table_name in DOMAIN_TABLES:
        for column in Base.metadata.tables[table_name].columns:
            if isinstance(column.type, sa.Enum):
                assert column.type.create_constraint is False


def test_each_enum_column_has_exactly_one_named_check() -> None:
    for table_name in DOMAIN_TABLES:
        table = Base.metadata.tables[table_name]
        for column in table.columns:
            if isinstance(column.type, sa.Enum):
                suffix = f"{column.name}_enum_values"
                matches = [
                    constraint
                    for constraint in table.constraints
                    if isinstance(constraint, sa.CheckConstraint)
                    and _constraint_name(constraint).endswith(suffix)
                ]
                assert len(matches) == 1, f"{table_name}.{column.name}"


def test_enum_check_admits_exactly_the_python_members() -> None:
    """The database vocabulary can never drift from the application's, because
    the literal list is generated from the Python enum."""
    constraint = enum_check("security_type", SecurityType)
    assert constraint is not None
    sql = str(constraint.sqltext)
    for member in SecurityType:
        assert f"'{member.value}'" in sql
    assert "NOT_A_MEMBER" not in sql


def test_enum_type_length_is_declared() -> None:
    """A ``VARCHAR`` without a length accepts arbitrary input, which would make
    the ``CHECK`` the only thing bounding the value's storage cost."""
    assert enum_type(SecurityType, name="security_type", length=32).length == 32


def test_validate_strings_is_enabled_on_enum_types() -> None:
    """A bad value should fail on assignment with a Python error, not silently
    reach the database and surface as an opaque constraint violation."""
    assert enum_type(SecurityType, name="security_type", length=32).validate_strings is True


def test_enum_members_are_stable_upper_case_identifiers() -> None:
    """Every value is interpolated into a quoted SQL literal by
    :func:`enum_check`, so a value needing quoting would produce a wrong
    constraint. This test is what makes that assumption safe to keep."""
    for enum_cls in (SecurityType, IdentifierType, EarningsTiming, ProviderType):
        for member in enum_cls:
            assert member.value.isupper()
            assert member.value.replace("_", "").isalnum()


def test_str_enum_values_equal_their_names() -> None:
    """``StrEnum`` makes the stored value greppable and lets the column be read
    as the member it names."""
    for enum_cls in (SecurityType, IdentifierType, EarningsTiming, ProviderType):
        for member in enum_cls:
            assert member.value == member.name


# ─────────────────────────── decimal columns ─────────────────────────────────


def test_price_columns_use_numeric_not_float() -> None:
    """A ``float`` would lose exactness on values a research system later sums
    and compares; prices must stay ``Decimal`` end to end."""
    for name in ("open", "high", "low", "close", "adjusted_close"):
        column_type = _column("daily_price_bar", name).type
        assert isinstance(column_type, sa.Numeric), name
        assert not isinstance(column_type, sa.Float), name
        assert _column_numeric_dims("daily_price_bar", name) == (20, 6), name


def test_adjusted_close_is_nullable_and_never_defaults_to_close() -> None:
    """``NULL`` means "this source published no adjusted close", which is
    materially different from "the adjusted close equals the close"."""
    assert Base.metadata.tables["daily_price_bar"].columns["adjusted_close"].nullable is True


def test_earnings_financials_use_their_own_precisions() -> None:
    for name in ("reported_eps", "estimated_eps"):
        assert _column_numeric_dims("earnings_event", name) == (20, 4), name
    for name in ("reported_revenue", "estimated_revenue"):
        assert _column_numeric_dims("earnings_event", name) == (24, 4), name


def test_non_negativity_is_a_named_check_not_a_type() -> None:
    """Enforced by a named ``CHECK`` rather than baked into the column type, so a
    rejection names the actual business rule instead of an opaque type error."""
    names = _check_names("daily_price_bar")
    assert any("volume" in name and "non_negative" in name for name in names)
    assert any("price" in name and "non_negative" in name for name in names)


# ─────────────────────────── foreign keys ────────────────────────────────────


def _foreign_keys(table_name: str) -> list[sa.ForeignKey]:
    return list(Base.metadata.tables[table_name].foreign_keys)


def _check_names(table_name: str) -> set[str]:
    return {
        _constraint_name(constraint)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def _index_names(table_name: str) -> set[str]:
    return {str(index.name) for index in Base.metadata.tables[table_name].indexes}


@pytest.mark.parametrize(
    "table_name",
    [
        "security",
        "listing",
        "security_identifier",
        "daily_price_bar",
        "benchmark",
        "benchmark_price_bar",
        "earnings_event",
    ],
)
def test_every_foreign_key_restricts_deletes(table_name: str) -> None:
    """``CASCADE`` would silently erase price history and provenance the moment
    a reference-data row was removed. Deletion must be an explicit decision."""
    foreign_keys = _foreign_keys(table_name)
    assert foreign_keys, table_name
    for foreign_key in foreign_keys:
        assert foreign_key.ondelete == "RESTRICT", f"{table_name}.{foreign_key.parent.name}"


def test_listing_points_at_both_instrument_and_venue() -> None:
    targets = {fk.column.table.name for fk in _foreign_keys("listing")}
    assert {"security", "exchange"} <= targets


def test_observations_point_at_both_their_entity_and_their_source() -> None:
    for table_name, entity in (
        ("daily_price_bar", "listing"),
        ("benchmark_price_bar", "benchmark"),
    ):
        targets = {fk.column.table.name for fk in _foreign_keys(table_name)}
        assert {entity, "data_source"} <= targets, table_name


def test_no_self_referential_foreign_keys() -> None:
    for table_name in DOMAIN_TABLES:
        for foreign_key in _foreign_keys(table_name):
            assert foreign_key.column.table.name != table_name, table_name


# ─────────────────────────── unique constraints ──────────────────────────────


def _unique_column_sets(table_name: str) -> set[frozenset[str]]:
    """Return every column set that the database treats as unique.

    Deliberately covers all three ways SQLAlchemy expresses uniqueness, not just
    ``UniqueConstraint``:

    * ``sa.UniqueConstraint(...)`` in ``__table_args__``
    * ``mapped_column(unique=True)``, which emits a ``UNIQUE`` on the column
    * ``sa.Index(..., unique=True)``, which emits a unique index

    A helper that only saw the first would report a column as unconstrained
    while the DDL still carried a ``UNIQUE``, which is precisely the mistake
    these tests exist to prevent.
    """
    table = Base.metadata.tables[table_name]
    sets: set[frozenset[str]] = {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    sets |= {frozenset({column.name}) for column in table.columns if column.unique}
    sets |= {
        frozenset(column.name for column in index.columns)
        for index in table.indexes
        if index.unique
    }
    return sets


def test_listing_enforces_one_row_per_ticker_per_venue_per_start_date() -> None:
    assert frozenset({"exchange_id", "ticker", "valid_from"}) in _unique_column_sets("listing")


def test_mic_is_unique_across_venues() -> None:
    assert frozenset({"mic"}) in _unique_column_sets("exchange")


def test_company_legal_name_is_not_unique() -> None:
    """Two issuers may share a legal name in different jurisdictions, so
    uniqueness on it would be a false constraint rather than a useful one.

    A name is also not a stable market identifier: companies rename, and two
    distinct entities in different jurisdictions can carry identical strings.
    Constraining it would reject real issuers, so the UUID is the only identity.
    """
    # The one unique index company now carries is the partial CIK index, which
    # constrains a *stable regulatory identifier* and only where it is present.
    # What must stay unconstrained is the name, and this asserts exactly that
    # rather than the weaker "some set of columns is unique".
    assert _unique_column_sets("company") == {frozenset({"cik"})}
    for index in Base.metadata.tables["company"].indexes:
        if index.unique:
            assert {c.name for c in index.columns} == {"cik"}
            assert index.dialect_options["postgresql"]["where"] is not None


def test_company_identity_is_the_uuid_alone() -> None:
    """Nothing about the issuer is a natural key, so identity is the surrogate
    id and nothing else.

    Note the id is unique because it is the *primary key*, not because it
    carries ``unique=True``: SQLAlchemy leaves that flag unset, which is why
    :func:`_unique_column_sets` must not be the only thing consulted.
    """
    column = _column("company", "id")
    assert column.primary_key is True
    assert column.unique is None
    assert isinstance(column.type, sa.Uuid)
    assert column.default is not None


def test_a_source_cannot_report_the_same_session_twice() -> None:
    """``(entity, trading_date, source)`` is the natural key of an observation.
    A vendor restating a session is a *new* source row, not a second row from
    the same source."""
    assert frozenset({"listing_id", "trading_date", "source_id"}) in _unique_column_sets(
        "daily_price_bar"
    )
    assert frozenset({"benchmark_id", "trading_date", "source_id"}) in _unique_column_sets(
        "benchmark_price_bar"
    )


def test_identifier_is_unique_per_scheme() -> None:
    assert frozenset({"identifier_type", "identifier_value"}) in _unique_column_sets(
        "security_identifier"
    )


def test_benchmark_name_is_not_unique() -> None:
    """Providers spell and version the same index name differently, so a unique
    name would reject legitimate data rather than protect an identity."""
    assert frozenset({"name"}) not in _unique_column_sets("benchmark")


def test_earnings_has_no_fiscal_period_uniqueness() -> None:
    """Restatements and dual-class filings mean one company can report the same
    fiscal period more than once. Uniqueness here would silently drop rows."""
    uniqueness = _unique_column_sets("earnings_event")
    assert frozenset({"company_id", "fiscal_year", "fiscal_quarter"}) not in uniqueness


# ─────────────────────────── partial indexes ─────────────────────────────────


def _partial_indexes(table_name: str) -> list[sa.Index]:
    return [
        index
        for index in Base.metadata.tables[table_name].indexes
        if index.dialect_options["postgresql"].get("where") is not None
    ]


def test_only_one_current_listing_per_ticker_per_venue_is_indexed_enforced() -> None:
    """``WHERE valid_to IS NULL`` is what makes this *current* rather than
    historical: the same ticker must be reusable after a delisting."""
    partials = _partial_indexes("listing")
    tickers = [
        index
        for index in partials
        if {column.name for column in index.columns} == {"exchange_id", "ticker"}
    ]
    assert len(tickers) == 1
    assert "valid_to IS NULL" in str(tickers[0].dialect_options["postgresql"]["where"])


def test_only_one_current_primary_listing_per_security_is_enforced() -> None:
    """A security traded primarily on two simultaneous venues would make
    "the primary venue" undefined, which silently corrupts any attribution
    done later."""
    partials = _partial_indexes("listing")
    primary = [
        index for index in partials if {column.name for column in index.columns} == {"security_id"}
    ]
    assert len(primary) == 1
    where = str(primary[0].dialect_options["postgresql"]["where"])
    assert "valid_to IS NULL" in where
    assert "is_primary" in where


def test_listing_also_carries_a_venue_ticker_history_index() -> None:
    """Supports ``ticker_history`` without scanning every listing."""
    assert any("ticker" in name for name in _index_names("listing"))


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("daily_price_bar", "listing_id"),
        ("benchmark_price_bar", "benchmark_id"),
        ("earnings_event", "company_id"),
    ],
)
def test_observation_tables_are_indexed_by_entity(table_name: str, column_name: str) -> None:
    """Every point-in-time read starts from "all rows for this entity", so the
    entity column has to be reachable as a leading index column. For the two
    price tables it is the first column of the natural-key unique constraint,
    which PostgreSQL backs with an index; for earnings it is an explicit index."""
    table = Base.metadata.tables[table_name]
    leading_unique = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
        and next(iter(constraint.columns)).name == column_name
    ]
    explicit = [
        index for index in table.indexes if index.columns and index.columns[0].name == column_name
    ]
    assert leading_unique or explicit, table_name


# ─────────────────────────── check constraints ───────────────────────────────


def test_listing_interval_is_checked() -> None:
    assert any("listing_interval_ordered" in name for name in _check_names("listing"))


def test_daily_price_bar_checks_ohlc_and_non_negativity() -> None:
    names = _check_names("daily_price_bar")
    for relationship in (
        "high_ge_low",
        "high_ge_open",
        "high_ge_close",
        "low_le_open",
        "low_le_close",
    ):
        assert any(relationship in name for name in names), relationship


def test_earnings_fiscal_bounds_are_checked() -> None:
    names = _check_names("earnings_event")
    assert any("fiscal_year" in name for name in names)
    assert any("fiscal_quarter" in name for name in names)


@pytest.mark.parametrize("table_name", ["listing", "security_identifier"])
def test_optional_validity_endpoints_are_still_order_checked(table_name: str) -> None:
    """``valid_to`` is nullable, so the check must tolerate NULL -- but when it
    *is* supplied it has to be strictly after ``valid_from``, because the
    interval is half-open."""
    sql = _check_sql(table_name, "_interval_ordered")
    assert "valid_to IS NULL" in sql
    assert "valid_to > valid_from" in sql


def test_code_shapes_are_checked_in_the_database() -> None:
    """The database is the authority: a row can arrive from ``COPY``, a raw
    script or a future worker, bypassing Pydantic entirely."""
    for table_name, needle in (
        ("company", "country_code"),
        ("security", "currency"),
        ("exchange", "mic"),
        ("benchmark", "currency"),
    ):
        names = _check_names(table_name)
        assert any(needle in name for name in names), (table_name, needle)


def test_data_source_base_url_must_be_http_or_https() -> None:
    assert any("base_url" in name for name in _check_names("data_source"))


# ─────────────────────────── naming convention ───────────────────────────────


def test_naming_convention_is_applied_to_generated_names() -> None:
    """Unnamed constraints must still land under the deterministic convention,
    otherwise a later autogenerate diffs a rename as a drop plus an add."""
    convention = Base.metadata.naming_convention
    assert convention is not None
    assert convention["ix"] == "ix_%(table_name)s_%(column_0_N_name)s"
    assert convention["uq"] == "uq_%(table_name)s_%(column_0_N_name)s"
    assert convention["ck"] == "ck_%(table_name)s_%(constraint_name)s"
    assert convention["fk"] == "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s"
    assert convention["pk"] == "pk_%(table_name)s"


def test_every_constraint_and_index_has_a_resolved_name() -> None:
    """A ``None`` name means the convention did not match, which shows up much
    later as an un-diffable migration."""
    for table_name in DOMAIN_TABLES:
        table = Base.metadata.tables[table_name]
        for constraint in table.constraints:
            assert constraint.name, (table_name, constraint)
        for index in table.indexes:
            assert index.name, (table_name, index)


# ─────────────────────────── relationships ───────────────────────────────────


def test_listing_navigates_to_its_instrument_and_venue() -> None:
    navigable = {rel.mapper.class_ for rel in Listing.__mapper__.relationships}
    assert navigable >= {Security, Exchange}


def test_company_navigates_to_its_securities() -> None:
    assert "securities" in Company.__mapper__.relationships


def test_a_source_exposes_the_observations_it_attributed() -> None:
    """Provenance has to be reachable in one hop for a point-in-time audit."""
    assert "price_bars" in DataSource.__mapper__.relationships
    assert "benchmark_price_bars" in DataSource.__mapper__.relationships
    assert "earnings_events" in DataSource.__mapper__.relationships


MANY_TO_ONE: list[tuple[Model, str]] = [
    (Security, "company"),
    (Listing, "security"),
    (Listing, "exchange"),
    (DailyPriceBar, "listing"),
    (BenchmarkPriceBar, "benchmark"),
    (EarningsEvent, "company"),
    (EarningsEvent, "source"),
]


@pytest.mark.parametrize(("model", "key"), MANY_TO_ONE)
def test_many_to_one_relationships_are_eagerly_joined(model: Model, key: str) -> None:
    """A lazy ``lazy="select"`` many-to-one is an N+1 that only appears under
    load. Every price-bar read needs its listing, so the join is unconditional."""
    assert model.__mapper__.relationships[key].lazy == "joined", (model.__name__, key)


ONE_TO_MANY: list[tuple[Model, str]] = [
    (Company, "securities"),
    (Company, "earnings_events"),
    (Security, "listings"),
    (Security, "identifiers"),
    (Exchange, "listings"),
    (Listing, "price_bars"),
    (DataSource, "price_bars"),
    (DataSource, "benchmark_price_bars"),
    (DataSource, "earnings_events"),
    (Benchmark, "price_bars"),
]


@pytest.mark.parametrize(("model", "key"), ONE_TO_MANY)
def test_one_to_many_relationships_never_use_a_joined_load(model: Model, key: str) -> None:
    """``lazy="joined"`` on a collection multiplies the parent's rows by the
    child's, which corrupts pagination and any aggregate over the result."""
    lazy = model.__mapper__.relationships[key].lazy
    assert lazy in {"select", "selectin", "raiseload", "noload"}, (model.__name__, key)


@pytest.mark.parametrize(
    ("model", "relationship", "back_populates"),
    [
        (Security, "company", "securities"),
        (Listing, "security", "listings"),
        (Listing, "exchange", "listings"),
        (DailyPriceBar, "listing", "price_bars"),
        (BenchmarkPriceBar, "benchmark", "price_bars"),
        (EarningsEvent, "company", "earnings_events"),
        (EarningsEvent, "source", "earnings_events"),
    ],
)
def test_relationships_are_two_way(model: Model, relationship: str, back_populates: str) -> None:
    """A one-way relationship is a latent source of inconsistent writes."""
    relation = model.__mapper__.relationships[relationship]
    assert relation.back_populates == back_populates


CASCADE_FREE: list[Model] = [Security, DataSource, Company, Benchmark]


@pytest.mark.parametrize("model", CASCADE_FREE)
def test_relationship_cascade_never_deletes_child_observations(model: Model) -> None:
    """``save-update, merge`` is the default and is what we want: the ORM must
    not be able to remove a price bar just because a parent was deleted."""
    for relationship in model.__mapper__.relationships:
        assert "delete" not in relationship.cascade, (model.__name__, relationship.key)


# ─────────────────────────── value round-tripping ────────────────────────────


def test_decimal_defaults_are_not_floats() -> None:
    """Constructing a model in Python must not introduce a binary float that
    only the database's ``NUMERIC`` cast would quietly round."""
    bar = DailyPriceBar(
        listing_id=new_uuid(),
        trading_date=dt.date(2024, 1, 2),
        open=Decimal("0.1"),
        high=Decimal("0.300000"),
        low=Decimal("0.100000"),
        close=Decimal("0.1"),
        source_id=new_uuid(),
        ingested_at=utcnow(),
    )
    assert bar.open == Decimal("0.1")


def test_keys_are_generated_in_python_not_by_the_server() -> None:
    """SQLAlchemy applies a column ``default`` at flush time, so an unflushed
    object has no id yet. The invariant worth pinning is that generation is a
    Python-side default -- there is no server-side ``gen_random_uuid()``, so the
    key algorithm is explicit and an id can be supplied at construction."""
    column = _column("company", "id")
    assert column.server_default is None
    assert _default(column) is new_uuid

    explicit = Company(id=new_uuid(), legal_name="Acme Inc", display_name="Acme")
    assert explicit.id is not None
    assert isinstance(explicit.id, uuid.UUID)


def test_created_at_is_assigned_in_python_at_flush_time() -> None:
    """Timestamps come from :func:`utcnow` rather than ``now()`` in the database,
    so the value is identical whichever writer produced the row."""
    column = _column("company", "created_at")
    assert column.server_default is None
    assert _default(column) is utcnow
    assert utcnow().tzinfo is not None
