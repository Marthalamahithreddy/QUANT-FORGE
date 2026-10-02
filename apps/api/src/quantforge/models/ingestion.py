"""Operational record of one ingestion execution.

What this is, and what it is not
--------------------------------
``IngestionRun`` is bookkeeping about *our* process: which provider we asked,
what we asked for, whether it worked, how many records arrived, and where the
raw evidence was written. It is not financial data, and it deliberately holds no
prices, no identifiers and no company facts -- those live in the domain tables
that the run produced.

It exists because ingestion is the part of a research system most likely to fail
silently. Without it, an empty price series is indistinguishable from "the
market was closed", "the provider returned nothing", "the request was never
sent", and "the request failed and nobody looked". A run row makes each of those
a distinguishable, queryable state, and it is the join target for answering "why
does this backtest have a hole in it".

Provenance linkage
------------------
``raw_artifact_path`` and ``raw_content_sha256`` point at the preserved response.
Storing the *hash* in PostgreSQL, not the bytes, is what makes the file on disk
trustworthy: a payload that has been altered no longer matches the digest the
database recorded when it was written. A nullable pair, because a run that died
before any response arrived has nothing to point at.

No secrets, ever
----------------
``request_parameters`` is JSONB and is written from the adapter's declared
non-secret parameters, and ``error_message`` is truncated and carries no request
URL. A credential leak into either column would be persisted, replicated to
every backup and readable by anything with database access, so the fields that
could plausibly carry one are constrained at the type boundary rather than by
convention alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from quantforge.db.base import Base
from quantforge.models.base import NAME_LENGTH, CreatedAtMixin, UUIDPrimaryKeyMixin
from quantforge.models.enums import (
    INGESTION_STATUS_LENGTH,
    IngestionStatus,
    enum_check,
    enum_type,
)

if TYPE_CHECKING:
    from quantforge.models.market import DailyPriceBar

__all__ = ["IngestionRun"]

#: Upper bound on a stored error message. A provider's HTML error page can be
#: arbitrarily long; the run row records a summary, not a transcript. Truncation
#: happens before the value reaches this column, so the limit is a backstop.
ERROR_MESSAGE_LENGTH: Final[int] = 1000

#: Upper bound on a provider name. Generous for "massive", tight enough to catch
#: a whole sentence being passed where an identifier was expected.
PROVIDER_LENGTH: Final[int] = 64

#: Upper bound on a dataset label, e.g. "daily-bars".
DATASET_LENGTH: Final[int] = 64


class IngestionRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One execution of one ingestion job against one provider."""

    __tablename__ = "ingestion_run"
    __table_args__ = (
        enum_check("status", IngestionStatus),
        # A run that has completed must say when; a run in flight must not.
        # Encoding that here means "succeeded but never finished" is impossible
        # to represent, instead of being discovered by a monitoring query.
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) OR "
            "(status <> 'running' AND completed_at IS NOT NULL)",
            name="completion_consistent",
        ),
        # A completed run reports what it did. Zero is a legitimate outcome (an
        # empty range is a valid answer), so this is a null check, not a >0 one.
        sa.CheckConstraint(
            "records_received IS NOT NULL AND records_written IS NOT NULL "
            "AND records_skipped IS NOT NULL AND records_failed IS NOT NULL",
            name="counts_present",
        ),
        # Operational dashboard: "what ran lately, and did it work".
        sa.Index("ix_ingestion_run_provider_started", "provider", "started_at"),
        # Tie an observation back to the run that produced it.
        sa.Index("ix_ingestion_run_status", "status"),
    )

    provider: Mapped[str] = mapped_column(
        sa.String(PROVIDER_LENGTH),
        nullable=False,
        doc="Stable provider identity, e.g. 'massive'. Matches DataSource.name.",
    )
    dataset: Mapped[str] = mapped_column(
        sa.String(DATASET_LENGTH),
        nullable=False,
        doc="Logical job, e.g. 'reference' or 'daily-bars'.",
    )
    status: Mapped[IngestionStatus] = mapped_column(
        enum_type(IngestionStatus, name="ingestion_status", length=INGESTION_STATUS_LENGTH),
        nullable=False,
        default=IngestionStatus.RUNNING,
        doc="Lifecycle state; 'running' means the row was never finalised.",
    )

    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        doc="When the job began.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
        doc="When the job reached a terminal state; null while running.",
    )

    request_parameters: Mapped[dict[str, object]] = mapped_column(
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        default=dict,
        doc=(
            "Non-secret request parameters. Secrets are never written here: a key "
            "persisted in this column would outlive the credential and land in "
            "every backup."
        ),
    )

    records_received: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        doc="Records the provider returned.",
    )
    records_written: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        doc="New canonical rows inserted.",
    )
    records_skipped: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        doc=(
            "Records already stored with identical canonical values. A re-run of "
            "the same range lands entirely here."
        ),
    )
    records_failed: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=0,
        doc="Records rejected as malformed or in conflict with stored values.",
    )

    raw_artifact_path: Mapped[str | None] = mapped_column(
        sa.String(NAME_LENGTH * 4),
        nullable=True,
        doc="Store-relative path of the preserved response; null if none arrived.",
    )
    raw_content_sha256: Mapped[str | None] = mapped_column(
        sa.String(64),
        nullable=True,
        doc=(
            "SHA-256 hex digest of the preserved bytes. Stored so a file that has "
            "been altered since can be detected."
        ),
    )
    raw_artifact_bytes: Mapped[int | None] = mapped_column(
        sa.Integer,
        nullable=True,
        doc="Size of the preserved response in bytes.",
    )

    error_type: Mapped[str | None] = mapped_column(
        sa.String(PROVIDER_LENGTH),
        nullable=True,
        doc="Exception class name, for grouping failures without storing text.",
    )
    error_message: Mapped[str | None] = mapped_column(
        sa.String(ERROR_MESSAGE_LENGTH),
        nullable=True,
        doc="Truncated, redacted failure summary. Never contains a credential.",
    )

    #: Rows this run wrote. The join target for "what did this run change?",
    #: which is the question a run row exists to be able to answer.
    price_bars: Mapped[list[DailyPriceBar]] = relationship(
        back_populates="ingestion_run",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"IngestionRun(id={self.id!r}, provider={self.provider!r}, "
            f"dataset={self.dataset!r}, status={self.status!r})"
        )

    def finalize(
        self,
        *,
        status: IngestionStatus,
        completed_at: datetime,
        **counts: int,
    ) -> None:
        """Move a run to a terminal state and record its outcome.

        Counts default to zero so a caller that only knows the status does not
        have to invent numbers. A ``running`` status is rejected here rather than
        reaching the ``CHECK`` constraint as an opaque IntegrityError.
        """
        if status is IngestionStatus.RUNNING:
            message = "finalize() requires a terminal status, not 'running'"
            raise ValueError(message)
        self.status = status
        self.completed_at = completed_at
        for field in ("records_received", "records_written", "records_skipped", "records_failed"):
            setattr(self, field, counts.get(field, 0))

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock duration, or ``None`` while the run is still in flight."""
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def identity(self) -> uuid.UUID:
        """Alias used by the CLI when reporting the run back to the operator."""
        return self.id
