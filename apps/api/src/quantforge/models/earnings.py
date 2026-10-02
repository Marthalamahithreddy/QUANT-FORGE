"""Corporate events: reported earnings results, as the source stated them.

This table records *facts as published*. It deliberately contains no
derived analytics. EPS surprise, revenue surprise, post-event returns, abnormal
returns and tradability are all computed later, in the analytics layer, where
the methodology is versioned and re-runnable. Persisting a surprise figure here
would freeze one methodology's answer into the fact table and make it
impossible to answer "what would we have computed with a different
definition?".

The same discipline drives the three temporal columns:

* ``event_date`` is a plain ``DATE``: the provider-supported calendar date of
  the event, exactly as the source stated it. It is **always required** --
  every earnings-calendar source this codebase has integrated states at least
  this much.
* ``event_timestamp`` is a nullable, timezone-aware ``TIMESTAMPTZ``: the exact
  instant the event occurred, *only* when the source genuinely states one. It
  is never derived from ``event_date`` or ``timing`` -- a calendar date plus
  "after market close" is not an instant, and manufacturing midnight, 09:30 or
  16:00 out of that pair would assert a precision the source never gave. It is
  *not* normalised to a "session boundary" when present, because doing so
  would pre-empt the tradability question.
* ``timing`` records the session phase the source declared -- and only that.
  ``BEFORE_MARKET`` covers both a 07:00 release and a 09:31 release, which
  have very different information content; collapsing them to a boolean or to a
  session date would lose exactly the detail point-in-time research needs. It
  does not imply or require an exact ``event_timestamp``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from quantforge.db.base import Base
from quantforge.models.base import (
    CURRENCY_LENGTH,
    MAX_FISCAL_QUARTER,
    MAX_FISCAL_YEAR,
    MIN_FISCAL_QUARTER,
    MIN_FISCAL_YEAR,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    eps_type,
    money_type,
    timestamptz,
    utcnow,
)
from quantforge.models.enums import (
    EARNINGS_TIMING_LENGTH,
    EarningsTiming,
    enum_check,
    enum_type,
)

if TYPE_CHECKING:
    from quantforge.models.company import Company
    from quantforge.models.provenance import DataSource

__all__ = ["EarningsEvent", "earnings_event_fingerprint"]


def earnings_event_fingerprint(  # noqa: PLR0913 - eleven factual fields are the point of the function
    *,
    fiscal_year: int,
    fiscal_quarter: int | None,
    event_date: date,
    event_timestamp: datetime | None,
    timing: EarningsTiming,
    reported_eps: Decimal | None,
    estimated_eps: Decimal | None,
    reported_revenue: Decimal | None,
    estimated_revenue: Decimal | None,
    currency: str | None,
    observed_at: datetime | None,
) -> str:
    """Deterministic SHA-256 over the factual fields of one observation.

    Two observations are the same fact exactly when this digest matches. The
    serialisation is canonical rather than raw, so equal values that are
    *spelled* differently still agree:

    * Decimals are normalised, so ``1.50`` and ``1.5`` collide.
    * Instants are normalised to UTC, so ``14:00+02:00`` and ``12:00Z`` collide.

    ``event_date`` and ``event_timestamp`` are both included, and deliberately
    kept distinct in the digest: a missing ``event_timestamp`` serialises as
    ``""``, which cannot collide with any real instant's ISO string (every
    real instant's ISO form is non-empty), so a date-only observation can
    never hash the same as one that happens to state midnight UTC. A change to
    *either* field -- the date or the exact instant -- changes the digest.

    The digest covers the observation's facts and nothing the writer adds --
    not the ticker (the unique index already carries ``company_id``), not
    ``ingested_at`` (the same fact re-fetched later must still collide). It is
    persisted in ``earnings_event.fingerprint`` and enforced by the
    ``uq_earnings_event_source_company_fingerprint`` index, which is why the
    cryptographic form (raw SHA-256 is a provable commitment) is used rather
    than Python's per-process-randomised built-in ``hash()``.

    This is the single definition shared by the ingestion writer and the model:
    ``earnings_fingerprint(CanonicalEarningsEvent)`` in the ingestion layer
    delegates here with the canonical fields, and the fingerprint stamped on
    every ORM write is derived through the same function, so the pipeline and
    the persistence layer can never disagree on what "the same fact" means.
    """
    parts = [
        fiscal_year,
        fiscal_quarter,
        event_date.isoformat(),
        _canonical_instant(event_timestamp),
        timing.value,
        _canonical_decimal(reported_eps),
        _canonical_decimal(estimated_eps),
        _canonical_decimal(reported_revenue),
        _canonical_decimal(estimated_revenue),
        currency,
        _canonical_instant(observed_at),
    ]
    # JSON with separators and ASCII output is deterministic across runs and
    # processes: no dict ordering, no locale, no trailing whitespace. The list's
    # order is fixed above, so position carries each field's meaning.
    material = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _canonical_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value.normalize())


def _canonical_instant(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(UTC).isoformat()


class EarningsEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reported earnings result for a company in a fiscal period.

    ``reported_eps``/``reported_revenue`` and their ``estimated_`` counterparts
    are stored side by side but never combined here. Keeping both is what makes
    a surprise computable later from whatever definition is chosen; collapsing
    them into a single surprise column at write time would discard the inputs.

    Reported and estimated figures are nullable individually because sources
    routinely publish one without the other: a preliminary release may state
    revenue but not EPS, and consensus estimates may exist for a company that
    has not yet reported.

    Neither EPS nor revenue is constrained to be non-negative. A loss-making
    quarter legitimately reports negative EPS, and negative revenue appears in
    restatements and reversals. A non-negative constraint here would reject
    exactly the periods a researcher most needs to see.

    ``fiscal_quarter`` is nullable and constrained to 1..4 when present.
    Companies reporting on non-quarterly cycles -- and annual-only filers -- do
    not have one, and inventing a value would corrupt the calendar joins future
    event studies will build.

    There is deliberately **no** uniqueness constraint on
    ``(company_id, fiscal_year, fiscal_quarter)``. A single fiscal period can
    legitimately have several rows: a preliminary release and a final one, a
    restatement, or two vendors reporting the same period. Forcing one row per
    period would force an overwrite policy at write time, which is precisely the
    source-attribution decision this schema exists to preserve. Period identity
    is expressed by the indexes plus a later reconciliation pass.

    Exact-observation identity lives elsewhere: ``fingerprint`` is a
    deterministic SHA-256 over the canonical factual fields, and the unique
    index ``uq_earnings_event_source_company_fingerprint`` enforces at most one
    row per ``(source_id, company_id, fingerprint)``. That is a finer key than
    the period triple, deliberately: two observations of the same period from
    the same source whose facts differ have different fingerprints and remain
    two rows (a revision is preserved, never overwritten), while a re-fetch of
    the identical fact hits the index and is counted as a duplicate instead of
    being stored twice.
    """

    __tablename__ = "earnings_event"
    __table_args__ = (
        enum_check("timing", EarningsTiming),
        sa.CheckConstraint(
            f"fiscal_year BETWEEN {MIN_FISCAL_YEAR} AND {MAX_FISCAL_YEAR}",
            name="fiscal_year_range",
        ),
        sa.CheckConstraint(
            (
                f"fiscal_quarter IS NULL "
                f"OR fiscal_quarter BETWEEN {MIN_FISCAL_QUARTER} AND {MAX_FISCAL_QUARTER}"
            ),
            name="fiscal_quarter_range",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="currency_iso4217",
        ),
        # Chronological access per company: "every event for this issuer, oldest
        # first" is the canonical query for event studies. Indexed on
        # event_date, not event_timestamp: the timestamp is frequently NULL,
        # while the date is the one temporal fact every source supplies.
        sa.Index("ix_earnings_event_company_event_date", "company_id", "event_date"),
        # Calendar access: "which periods does this issuer report?"
        sa.Index(
            "ix_earnings_event_company_fiscal",
            "company_id",
            "fiscal_year",
            "fiscal_quarter",
        ),
        sa.Index("ix_earnings_event_source_id", "source_id"),
        sa.Index("ix_earnings_event_ingestion_run_id", "ingestion_run_id"),
        # Exact-observation identity: one row per (source, company, fact set).
        # The digest column is NOT NULL, so "two rows with same fingerprint but
        # a missing optional field" -- which a plain multi-column unique can
        # never catch because it treats NULLs as distinct -- is still a unique
        # collision. See the class docstring for how this coexists with the
        # deliberate absence of a period-triple constraint.
        sa.UniqueConstraint(
            "source_id",
            "company_id",
            "fingerprint",
            name="uq_earnings_event_source_company_fingerprint",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Issuer the event belongs to.",
    )
    fiscal_year: Mapped[int] = mapped_column(
        sa.SmallInteger,
        nullable=False,
        doc="Issuer's fiscal year the result covers, not the calendar year.",
    )
    fiscal_quarter: Mapped[int | None] = mapped_column(
        sa.SmallInteger,
        nullable=True,
        doc="Issuer's fiscal quarter 1-4; NULL for non-quarterly/annual filers.",
    )
    event_date: Mapped[date] = mapped_column(
        sa.Date,
        nullable=False,
        doc=(
            "Provider-supported calendar date of the event, exactly as the "
            "source stated it. Always populated; never derived from a fiscal "
            "period or from event_timestamp."
        ),
    )
    event_timestamp: Mapped[datetime | None] = mapped_column(
        timestamptz(),
        nullable=True,
        doc=(
            "Exact instant the event occurred, timezone-aware, only when the "
            "source genuinely states one. NULL means the source gave a date "
            "and/or a coarse session only -- never derived from event_date or "
            "timing. Not snapped to a session boundary when present."
        ),
    )
    timing: Mapped[EarningsTiming] = mapped_column(
        enum_type(EarningsTiming, name="earnings_timing", length=EARNINGS_TIMING_LENGTH),
        nullable=False,
        default=EarningsTiming.UNKNOWN,
        server_default=EarningsTiming.UNKNOWN.value,
        doc="Session phase the source declared. Not a tradability judgement.",
    )
    reported_eps: Mapped[Decimal | None] = mapped_column(
        eps_type(),
        nullable=True,
        doc="Reported diluted/basic EPS as published. May be negative (a loss).",
    )
    estimated_eps: Mapped[Decimal | None] = mapped_column(
        eps_type(),
        nullable=True,
        doc="Consensus/market EPS estimate as published. May be negative.",
    )
    reported_revenue: Mapped[Decimal | None] = mapped_column(
        money_type(),
        nullable=True,
        doc="Reported revenue as published. May be negative after restatement.",
    )
    estimated_revenue: Mapped[Decimal | None] = mapped_column(
        money_type(),
        nullable=True,
        doc="Consensus/market revenue estimate as published.",
    )
    currency: Mapped[str | None] = mapped_column(
        sa.String(CURRENCY_LENGTH),
        nullable=True,
        doc="ISO 4217 code for the EPS/revenue figures, if stated by the source.",
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Provider that published this event.",
    )
    fingerprint: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        doc=(
            "Deterministic SHA-256 hex over the canonical factual fields of the "
            "observation (fiscal year/quarter, event date, event timestamp, "
            "timing, all four figures, currency, observed_at). Equal facts from the same "
            "source and company produce the same digest; any change produces a "
            "different one, which is what lets a revision coexist with the "
            "original row. Never caller-supplied: the model derives it from the "
            "row's own facts on every write, and the ingestion writer computes "
            "the same value from the same canonical fields."
        ),
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        timestamptz(),
        nullable=True,
        doc=(
            "When the source indicated this event became public, if known. NULL "
            "means undisclosed, not 'equal to ingested_at'."
        ),
    )
    ingested_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        doc="When QuantForge received and stored this event (UTC).",
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("ingestion_run.id", ondelete="RESTRICT"),
        nullable=True,
        doc=(
            "Run that wrote this event, when it arrived through ingestion. The "
            "link that answers 'which request produced this row?' without "
            "archaeology; null when the event was created outside a run."
        ),
    )

    company: Mapped[Company] = relationship(back_populates="earnings_events", lazy="joined")
    source: Mapped[DataSource] = relationship(
        back_populates="earnings_events",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"EarningsEvent(company_id={self.company_id!r}, "
            f"fy={self.fiscal_year!r}, fq={self.fiscal_quarter!r}, "
            f"date={self.event_date!r}, ts={self.event_timestamp!r})"
        )


def _derive_earnings_fingerprint(
    _mapper: Mapper[EarningsEvent], _connection: Connection, target: EarningsEvent
) -> None:
    """Stamp an exact-observation identity on every ORM write.

    ``fingerprint`` is derived data about the row, so the source of truth is
    the row itself: every insert or update recomputes it from the row's own
    facts, which makes the value correct by construction instead of by caller
    discipline. (The ingestion writer stamps it inside a Core-level ``INSERT``
    that bypasses these events, and uses the same ``earnings_event_fingerprint``
    definition, so the two paths cannot drift.)

    ``before_insert`` fires before Python-side column defaults are applied, so
    an unspecified ``timing`` is still ``None`` here even though the mapped type
    says it cannot be; the row will store the column's default, ``UNKNOWN``, so
    the effective value is what feeds the digest -- otherwise a bare insert and
    an explicit ``UNKNOWN`` insert would digest differently for the same stored
    fact.
    """
    target.fingerprint = earnings_event_fingerprint(
        fiscal_year=target.fiscal_year,
        fiscal_quarter=target.fiscal_quarter,
        event_date=target.event_date,
        event_timestamp=target.event_timestamp,
        timing=cast(EarningsTiming | None, target.timing) or EarningsTiming.UNKNOWN,
        reported_eps=target.reported_eps,
        estimated_eps=target.estimated_eps,
        reported_revenue=target.reported_revenue,
        estimated_revenue=target.estimated_revenue,
        currency=target.currency,
        observed_at=target.observed_at,
    )


sa.event.listen(EarningsEvent, "before_insert", _derive_earnings_fingerprint)
sa.event.listen(EarningsEvent, "before_update", _derive_earnings_fingerprint)
