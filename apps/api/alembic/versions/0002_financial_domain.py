"""financial domain: issuer, instrument, venue, observation and provenance schema.

Creates the canonical point-in-time schema for STEP 5.

Design notes a reviewer needs before reading the generated DDL:

* Every enum is a ``VARCHAR`` plus a named ``CHECK`` rather than a native
  PostgreSQL ``ENUM``. A native enum can never drop or rename a value and makes
  every addition a hand-written migration; a ``CHECK`` is an ordinary
  constraint. The corollary is that *adding* an enum member still needs a
  hand-written migration, because Alembic does not diff ``CHECK`` bodies.
* All foreign keys are ``ON DELETE RESTRICT``. Deleting reference data must be a
  deliberate act, never a cascade that silently removes price history.
* Observation tables (``daily_price_bar``, ``benchmark_price_bar``) deliberately
  have no ``updated_at``: a market observation is an immutable source fact, and
  a correction arrives as a new source-attributed row.
* ``data_source`` and ``security_identifier`` also omit ``updated_at`` for the
  same reason -- a registered source and an external code assignment are facts,
  not editable attributes.
* The two partial unique indexes on ``listing`` are what make "the ticker as of
  date d" unambiguous while still allowing a ticker to be reused later.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "company",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("sector", sa.String(length=255), nullable=True),
        sa.Column("industry", sa.String(length=255), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("founded_date", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name=op.f("ck_company_country_code_iso3166_alpha2"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company")),
    )
    op.create_index("ix_company_country_code", "company", ["country_code"], unique=False)
    op.create_index("ix_company_display_name", "company", ["display_name"], unique=False)
    op.create_table(
        "data_source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "provider_type",
            sa.Enum(
                "REGULATORY_FILING",
                "EXCHANGE_FEED",
                "MARKET_DATA_VENDOR",
                "BROKER_API",
                "MANUAL",
                "OTHER",
                name="provider_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "base_url IS NULL OR base_url ~ '^https?://'",
            name=op.f("ck_data_source_base_url_scheme"),
        ),
        sa.CheckConstraint(
            "provider_type IN ('REGULATORY_FILING', 'EXCHANGE_FEED', 'MARKET_DATA_VENDOR', 'BROKER_API', 'MANUAL', 'OTHER')",
            name=op.f("ck_data_source_provider_type_enum_values"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_source")),
        sa.UniqueConstraint("name", name=op.f("uq_data_source_name")),
    )
    op.create_table(
        "exchange",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mic", sa.String(length=4), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("acronym", sa.String(length=128), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name=op.f("ck_exchange_country_code_iso3166_alpha2"),
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'", name=op.f("ck_exchange_currency_iso4217")
        ),
        sa.CheckConstraint("mic ~ '^[A-Z0-9]{4}$'", name=op.f("ck_exchange_mic_iso10383")),
        sa.CheckConstraint("timezone <> ''", name=op.f("ck_exchange_timezone_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exchange")),
        sa.UniqueConstraint("mic", name=op.f("uq_exchange_mic")),
    )
    op.create_index("ix_exchange_country_code", "exchange", ["country_code"], unique=False)
    op.create_table(
        "benchmark",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_benchmark_currency_iso4217"),
        ),
        sa.CheckConstraint(
            "symbol IS NULL OR symbol <> ''", name=op.f("ck_benchmark_symbol_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_benchmark_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark")),
    )
    op.create_index("ix_benchmark_name", "benchmark", ["name"], unique=False)
    op.create_table(
        "earnings_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger(), nullable=False),
        sa.Column("fiscal_quarter", sa.SmallInteger(), nullable=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "timing",
            sa.Enum(
                "BEFORE_MARKET",
                "DURING_MARKET",
                "AFTER_MARKET",
                "UNKNOWN",
                name="earnings_timing",
                native_enum=False,
                length=32,
            ),
            server_default="UNKNOWN",
            nullable=False,
        ),
        sa.Column("reported_eps", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("estimated_eps", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("reported_revenue", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("estimated_revenue", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_earnings_event_currency_iso4217"),
        ),
        sa.CheckConstraint(
            "timing IN ('BEFORE_MARKET', 'DURING_MARKET', 'AFTER_MARKET', 'UNKNOWN')",
            name=op.f("ck_earnings_event_timing_enum_values"),
        ),
        sa.CheckConstraint(
            "fiscal_quarter IS NULL OR fiscal_quarter BETWEEN 1 AND 4",
            name=op.f("ck_earnings_event_fiscal_quarter_range"),
        ),
        sa.CheckConstraint(
            "fiscal_year BETWEEN 1900 AND 2200", name=op.f("ck_earnings_event_fiscal_year_range")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name=op.f("fk_earnings_event_company_id_company"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_earnings_event_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_earnings_event")),
    )
    op.create_index(
        "ix_earnings_event_company_event_ts",
        "earnings_event",
        ["company_id", "event_timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_earnings_event_company_fiscal",
        "earnings_event",
        ["company_id", "fiscal_year", "fiscal_quarter"],
        unique=False,
    )
    op.create_index("ix_earnings_event_source_id", "earnings_event", ["source_id"], unique=False)
    op.create_table(
        "security",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column(
            "security_type",
            sa.Enum(
                "EQUITY",
                "PREFERRED_EQUITY",
                "DEPOSITORY_RECEIPT",
                "ETF",
                "MUTUAL_FUND",
                "BOND",
                "OTHER",
                name="security_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_security_currency_iso4217")),
        sa.CheckConstraint(
            "security_type IN ('EQUITY', 'PREFERRED_EQUITY', 'DEPOSITORY_RECEIPT', 'ETF', 'MUTUAL_FUND', 'BOND', 'OTHER')",
            name=op.f("ck_security_security_type_enum_values"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name=op.f("fk_security_company_id_company"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security")),
        sa.UniqueConstraint("company_id", "name", name="uq_security_company_id_name"),
    )
    op.create_table(
        "benchmark_price_bar",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("benchmark_id", sa.Uuid(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("close >= 0", name=op.f("ck_benchmark_price_bar_close_non_negative")),
        sa.ForeignKeyConstraint(
            ["benchmark_id"],
            ["benchmark.id"],
            name=op.f("fk_benchmark_price_bar_benchmark_id_benchmark"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_benchmark_price_bar_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_price_bar")),
        sa.UniqueConstraint(
            "benchmark_id",
            "trading_date",
            "source_id",
            name="uq_benchmark_price_bar_benchmark_date_source",
        ),
    )
    op.create_index(
        "ix_benchmark_price_bar_source_id", "benchmark_price_bar", ["source_id"], unique=False
    )
    op.create_table(
        "listing",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("security_id", sa.Uuid(), nullable=False),
        sa.Column("exchange_id", sa.Uuid(), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("ticker <> ''", name=op.f("ck_listing_ticker_not_blank")),
        sa.CheckConstraint(
            "ticker = upper(ticker)", name=op.f("ck_listing_ticker_canonical_upper")
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name=op.f("ck_listing_listing_interval_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["exchange_id"],
            ["exchange.id"],
            name=op.f("fk_listing_exchange_id_exchange"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["security.id"],
            name=op.f("fk_listing_security_id_security"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listing")),
        sa.UniqueConstraint(
            "exchange_id", "ticker", "valid_from", name="uq_listing_exchange_id_ticker_valid_from"
        ),
    )
    op.create_index("ix_listing_security_id", "listing", ["security_id"], unique=False)
    op.create_index(
        "uq_listing_current_exchange_ticker",
        "listing",
        ["exchange_id", "ticker"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        "uq_listing_current_primary_security",
        "listing",
        ["security_id"],
        unique=True,
        postgresql_where=sa.text("is_primary AND valid_to IS NULL"),
    )
    op.create_table(
        "security_identifier",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("security_id", sa.Uuid(), nullable=False),
        sa.Column(
            "identifier_type",
            sa.Enum(
                "ISIN",
                "CUSIP",
                "SEDOL",
                "FIGI",
                "OTHER",
                name="identifier_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("identifier_value", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "identifier_type IN ('ISIN', 'CUSIP', 'SEDOL', 'FIGI', 'OTHER')",
            name=op.f("ck_security_identifier_identifier_type_enum_values"),
        ),
        sa.CheckConstraint(
            "identifier_value <> ''", name=op.f("ck_security_identifier_identifier_value_not_blank")
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR (valid_from IS NOT NULL AND valid_to > valid_from)",
            name=op.f("ck_security_identifier_identifier_interval_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["security.id"],
            name=op.f("fk_security_identifier_security_id_security"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_security_identifier_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_identifier")),
        sa.UniqueConstraint(
            "identifier_type", "identifier_value", name="uq_security_identifier_type_value"
        ),
    )
    op.create_index(
        "ix_security_identifier_security_id", "security_identifier", ["security_id"], unique=False
    )
    op.create_index(
        "ix_security_identifier_source_id", "security_identifier", ["source_id"], unique=False
    )
    op.create_table(
        "daily_price_bar",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("adjusted_close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "adjusted_close IS NULL OR adjusted_close >= 0",
            name=op.f("ck_daily_price_bar_adjusted_close_non_negative"),
        ),
        sa.CheckConstraint("close >= 0", name=op.f("ck_daily_price_bar_close_non_negative")),
        sa.CheckConstraint("high >= 0", name=op.f("ck_daily_price_bar_high_non_negative")),
        sa.CheckConstraint("high >= close", name=op.f("ck_daily_price_bar_high_ge_close")),
        sa.CheckConstraint("high >= low", name=op.f("ck_daily_price_bar_high_ge_low")),
        sa.CheckConstraint("high >= open", name=op.f("ck_daily_price_bar_high_ge_open")),
        sa.CheckConstraint("low <= close", name=op.f("ck_daily_price_bar_low_le_close")),
        sa.CheckConstraint("low <= open", name=op.f("ck_daily_price_bar_low_le_open")),
        sa.CheckConstraint("low >= 0", name=op.f("ck_daily_price_bar_low_non_negative")),
        sa.CheckConstraint("open >= 0", name=op.f("ck_daily_price_bar_open_non_negative")),
        sa.CheckConstraint(
            "volume IS NULL OR volume >= 0", name=op.f("ck_daily_price_bar_volume_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name=op.f("fk_daily_price_bar_listing_id_listing"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_daily_price_bar_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_price_bar")),
        sa.UniqueConstraint(
            "listing_id", "trading_date", "source_id", name="uq_daily_price_bar_listing_date_source"
        ),
    )
    op.create_index("ix_daily_price_bar_source_id", "daily_price_bar", ["source_id"], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_daily_price_bar_source_id", table_name="daily_price_bar")
    op.drop_table("daily_price_bar")
    op.drop_index("ix_security_identifier_source_id", table_name="security_identifier")
    op.drop_index("ix_security_identifier_security_id", table_name="security_identifier")
    op.drop_table("security_identifier")
    op.drop_index(
        "uq_listing_current_primary_security",
        table_name="listing",
        postgresql_where=sa.text("is_primary AND valid_to IS NULL"),
    )
    op.drop_index(
        "uq_listing_current_exchange_ticker",
        table_name="listing",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.drop_index("ix_listing_security_id", table_name="listing")
    op.drop_table("listing")
    op.drop_index("ix_benchmark_price_bar_source_id", table_name="benchmark_price_bar")
    op.drop_table("benchmark_price_bar")
    op.drop_table("security")
    op.drop_index("ix_earnings_event_source_id", table_name="earnings_event")
    op.drop_index("ix_earnings_event_company_fiscal", table_name="earnings_event")
    op.drop_index("ix_earnings_event_company_event_ts", table_name="earnings_event")
    op.drop_table("earnings_event")
    op.drop_index("ix_benchmark_name", table_name="benchmark")
    op.drop_table("benchmark")
    op.drop_index("ix_exchange_country_code", table_name="exchange")
    op.drop_table("exchange")
    op.drop_table("data_source")
    op.drop_index("ix_company_display_name", table_name="company")
    op.drop_index("ix_company_country_code", table_name="company")
    op.drop_table("company")
    # ### end Alembic commands ###
