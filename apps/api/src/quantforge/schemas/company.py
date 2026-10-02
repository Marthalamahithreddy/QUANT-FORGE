"""Company (issuer) schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from quantforge.models.base import (
    COUNTRY_LENGTH,
    DESCRIPTION_LENGTH,
    NAME_LENGTH,
    URL_LENGTH,
)
from quantforge.schemas.common import CodeValidatingSchema, OrmReadSchema, blank_to_none

__all__ = ["CompanyCreate", "CompanyRead"]


def _today() -> date:
    """The application's canonical "today" for date-only validation.

    UTC, deliberately, not the host machine's (or a request's) local zone.
    ``founded_date`` is a civil date with no venue and no submitting user to
    anchor a "local" today to, and every other clock in this schema
    (``ingested_at``, ``observed_at``) is already UTC. Picking the machine's
    local zone here would make validation depend on where the process happens
    to run rather than on a single, documented policy -- and near local
    midnight in a zone ahead of UTC, it would reject a same-day date that this
    policy accepts. Factored out so a test can hold it fixed instead of
    racing the real clock at a day boundary.
    """
    return datetime.now(UTC).date()


class CompanyCreate(CodeValidatingSchema):
    """Payload for registering a new issuer.

    ``legal_name`` and ``display_name`` are both required because a legal name is
    not always usable as a display name (holding companies, dual listings) and
    the difference is meaningful in research output. Neither is unique: distinct
    legal entities share names across jurisdictions, and identity is the UUID
    assigned by the database.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    legal_name: str = Field(min_length=1, max_length=NAME_LENGTH)
    display_name: str = Field(min_length=1, max_length=NAME_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_LENGTH)
    sector: str | None = Field(default=None, max_length=NAME_LENGTH)
    industry: str | None = Field(default=None, max_length=NAME_LENGTH)
    country_code: str | None = Field(default=None, max_length=COUNTRY_LENGTH)
    website: str | None = Field(default=None, max_length=URL_LENGTH)
    founded_date: date | None = None

    @field_validator("founded_date")
    @classmethod
    def _not_in_the_future(cls, value: date | None) -> date | None:
        """Reject a founding date after the application's canonical today.

        Enforced here rather than by a ``CHECK`` because PostgreSQL forbids
        non-``IMMUTABLE`` expressions in a ``CHECK``, and because "not in the
        future" is a moving target a stored constraint could not re-evaluate.
        """
        if value is not None and value > _today():
            message = f"founded_date must not be in the future, got {value!r}."
            raise ValueError(message)
        return value

    @field_validator("description", "sector", "industry", "website", mode="before")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """Normalise empty source strings to ``None``."""
        return blank_to_none(value)


class CompanyRead(OrmReadSchema):
    """Read model for an issuer.

    Note the absence of any ticker field. A company does not own a ticker; the
    tickers it trades under come from its securities' listings.
    """

    id: UUID
    legal_name: str
    display_name: str
    description: str | None
    sector: str | None
    industry: str | None
    country_code: str | None
    website: str | None
    founded_date: date | None
    created_at: datetime
    updated_at: datetime
