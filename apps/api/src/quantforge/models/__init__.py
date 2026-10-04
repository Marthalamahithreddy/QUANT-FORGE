"""QuantForge financial domain ORM models.

Importing this package registers every table on ``quantforge.db.Base.metadata``.
``alembic/env.py`` imports it explicitly, which is what makes autogenerate see
the full schema instead of an empty metadata object.

Layering
--------
``Company`` issues ``Security``; a ``Security`` trades as a ``Listing`` on an
``Exchange`` under a time-validity ticker; ``SecurityIdentifier`` holds the
external codes naming a security. ``DailyPriceBar`` observes a listing,
``BenchmarkPriceBar`` observes a benchmark, and ``EarningsEvent`` observes a
company. All three observation types carry a mandatory ``DataSource``
reference, so every stored fact is attributable.
"""

from __future__ import annotations

from quantforge.models.base import (
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    new_uuid,
    utcnow,
)
from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.corporate_action import StockSplit, StockSplitCoverage
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    AvailabilityBasis,
    EarningsTiming,
    FinancialFactSourceKind,
    IdentifierType,
    IngestionStatus,
    ListingFactKind,
    PriceBasis,
    ProviderType,
    ReproducibilityGap,
    ReproducibilityLevel,
    SecFormType,
    SecurityType,
    StockSplitAdjustmentType,
)
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import (
    FinancialFact,
    FinancialFactArtifact,
    FinancialFactSubmission,
)
from quantforge.models.ingestion import IngestionRun
from quantforge.models.listing_history import ListingHistoryFact
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.research_record import ResearchObservationRecord
from quantforge.models.security import Exchange, Listing, Security, SecurityIdentifier

#: The financial-domain tables, in dependency order. Useful for introspection
#: and for asserting in tests that nothing unexpected was declared.
DOMAIN_TABLES: tuple[str, ...] = (
    "data_source",
    "company",
    "security",
    "exchange",
    "listing",
    "security_identifier",
    "daily_price_bar",
    "benchmark",
    "benchmark_price_bar",
    "earnings_event",
    "sec_filing",
    "stock_split",
    "stock_split_coverage",
    "listing_history_fact",
    "financial_fact_artifact",
    "financial_fact_submission",
    "financial_fact",
)

#: Operational tables. Not financial data: these record what the ingestion
#: pipeline did, not what a market did. Kept separate from ``DOMAIN_TABLES`` so
#: a test asserting the financial schema is complete does not silently grow to
#: include bookkeeping, and vice versa.
OPERATIONAL_TABLES: tuple[str, ...] = ("ingestion_run", "research_observation_record")

#: Every table registered on ``Base.metadata``.
ALL_TABLES: tuple[str, ...] = DOMAIN_TABLES + OPERATIONAL_TABLES

__all__ = [
    "ALL_TABLES",
    "DOMAIN_TABLES",
    "OPERATIONAL_TABLES",
    "AvailabilityBasis",
    "Benchmark",
    "BenchmarkPriceBar",
    "Company",
    "CreatedAtMixin",
    "DailyPriceBar",
    "DataSource",
    "EarningsEvent",
    "EarningsTiming",
    "Exchange",
    "FinancialFact",
    "FinancialFactArtifact",
    "FinancialFactSourceKind",
    "FinancialFactSubmission",
    "IdentifierType",
    "IngestionRun",
    "IngestionStatus",
    "Listing",
    "ListingFactKind",
    "ListingHistoryFact",
    "PriceBasis",
    "ProviderType",
    "ReproducibilityGap",
    "ReproducibilityLevel",
    "ResearchObservationRecord",
    "SecFiling",
    "SecFormType",
    "Security",
    "SecurityIdentifier",
    "SecurityType",
    "StockSplit",
    "StockSplitAdjustmentType",
    "StockSplitCoverage",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "new_uuid",
    "utcnow",
]
