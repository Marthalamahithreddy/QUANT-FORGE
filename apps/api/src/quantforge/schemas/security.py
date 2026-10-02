"""Exchange, security, listing and identifier schemas."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantforge.models.base import (
    COUNTRY_LENGTH,
    CURRENCY_LENGTH,
    IDENTIFIER_VALUE_LENGTH,
    MIC_LENGTH,
    NAME_LENGTH,
    SHORT_NAME_LENGTH,
    TIMEZONE_LENGTH,
)
from quantforge.models.enums import IdentifierType, SecurityType
from quantforge.schemas.common import CodeValidatingSchema, OrmReadSchema, blank_to_none

__all__ = [
    "ExchangeCreate",
    "ExchangeRead",
    "ListingCreate",
    "ListingRead",
    "SecurityCreate",
    "SecurityIdentifierCreate",
    "SecurityIdentifierRead",
    "SecurityRead",
]


class ExchangeCreate(CodeValidatingSchema):
    """Payload for registering a trading venue.

    ``mic`` is the identity and the only uniqueness constraint on this model.
    ``name`` is descriptive and may repeat or change: venues are rebranded and
    vendors spell the same venue differently.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mic: str = Field(max_length=MIC_LENGTH)
    name: str = Field(min_length=1, max_length=NAME_LENGTH)
    acronym: str | None = Field(default=None, max_length=SHORT_NAME_LENGTH)
    country_code: str | None = Field(default=None, max_length=COUNTRY_LENGTH)
    timezone: str = Field(max_length=TIMEZONE_LENGTH)
    currency: str | None = Field(default=None, max_length=CURRENCY_LENGTH)
    is_active: bool = True

    @field_validator("acronym", mode="before")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """Normalise an empty acronym to ``None``."""
        return blank_to_none(value)


class ExchangeRead(OrmReadSchema):
    """Read model for a venue."""

    id: UUID
    mic: str
    name: str
    acronym: str | None
    country_code: str | None
    timezone: str
    currency: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SecurityCreate(CodeValidatingSchema):
    """Payload for registering an instrument issued by a company.

    ``security_type`` is a closed vocabulary rather than free text so that a
    price series can be interpreted correctly downstream: an ADR and a fund
    share are not equity in the same sense.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_id: UUID
    security_type: SecurityType = SecurityType.EQUITY
    name: str = Field(min_length=1, max_length=NAME_LENGTH)
    currency: str = Field(max_length=CURRENCY_LENGTH)
    is_active: bool = True


class SecurityRead(OrmReadSchema):
    """Read model for an instrument."""

    id: UUID
    company_id: UUID
    security_type: SecurityType
    name: str
    currency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ListingCreate(BaseModel):
    """Payload for recording that a security trades under a ticker on a venue.

    Validity is the half-open interval ``[valid_from, valid_to)``: ``valid_from``
    is inclusive, ``valid_to`` is exclusive, and ``valid_to = None`` means the
    listing is still current. Two consecutive periods may therefore share an
    endpoint, which is what makes a clean ticker handover expressible.

    ``ticker`` is canonicalised to upper case by the validator, matching the
    database ``CHECK``. Doing it here rather than trusting the caller is what
    guarantees ``aapl`` and ``AAPL`` cannot become two different listings.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    security_id: UUID
    exchange_id: UUID
    ticker: str = Field(min_length=1, max_length=32)
    valid_from: date
    valid_to: date | None = None
    is_primary: bool = False

    @field_validator("ticker", mode="before")
    @classmethod
    def _canonicalise_ticker(cls, value: str) -> str:
        """Upper-case the ticker and reject a blank symbol."""
        normalised = blank_to_none(value)
        if normalised is None:
            message = "ticker must not be blank."
            raise ValueError(message)
        return normalised.upper()

    @model_validator(mode="after")
    def _check_interval(self) -> ListingCreate:
        """Reject an empty or inverted validity interval.

        Mirrors ``ck_listing_listing_interval_ordered``. Doing it here turns a
        confusing constraint violation into an actionable field error.
        """
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            message = (
                "valid_to is exclusive and must be strictly after valid_from "
                f"(half-open [valid_from, valid_to)); got valid_from={self.valid_from!r}, "
                f"valid_to={self.valid_to!r}."
            )
            raise ValueError(message)
        return self


class ListingRead(OrmReadSchema):
    """Read model for a venue listing.

    Carries the security and exchange ids but no nested objects: a listing read
    is a flat identity, and eagerly nesting the whole instrument graph into every
    price-bar read is the N+1 this schema is designed to avoid.
    """

    id: UUID
    security_id: UUID
    exchange_id: UUID
    ticker: str
    valid_from: date
    valid_to: date | None
    is_primary: bool
    created_at: datetime
    updated_at: datetime


class SecurityIdentifierCreate(BaseModel):
    """Payload for attaching an external code to a security.

    Both validity endpoints are optional because a source may never disclose an
    identifier's effective dates. If ``valid_to`` is supplied, ``valid_from``
    must be too -- an interval unbounded below cannot satisfy the ordering rule.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    security_id: UUID
    identifier_type: IdentifierType
    identifier_value: str = Field(min_length=1, max_length=IDENTIFIER_VALUE_LENGTH)
    valid_from: date | None = None
    valid_to: date | None = None
    source_id: UUID | None = None

    @field_validator("identifier_value", mode="before")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Reject a blank identifier value."""
        normalised = blank_to_none(value)
        if normalised is None:
            message = "identifier_value must not be blank."
            raise ValueError(message)
        return normalised

    @model_validator(mode="after")
    def _check_interval(self) -> SecurityIdentifierCreate:
        """Validate the optional half-open validity interval."""
        if self.valid_to is not None:
            if self.valid_from is None:
                message = "valid_from is required when valid_to is supplied."
                raise ValueError(message)
            if self.valid_to <= self.valid_from:
                message = (
                    "valid_to is exclusive and must be strictly after valid_from; got "
                    f"valid_from={self.valid_from!r}, valid_to={self.valid_to!r}."
                )
                raise ValueError(message)
        return self


class SecurityIdentifierRead(OrmReadSchema):
    """Read model for an external identifier."""

    id: UUID
    security_id: UUID
    identifier_type: IdentifierType
    identifier_value: str
    valid_from: date | None
    valid_to: date | None
    source_id: UUID | None
    created_at: datetime
