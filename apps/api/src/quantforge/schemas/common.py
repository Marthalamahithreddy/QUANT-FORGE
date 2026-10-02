"""Shared Pydantic configuration and field validators for the domain schemas.

The database ``CHECK`` constraints are the authority for code shapes and interval
ordering. These validators exist so an invalid value is rejected at the
application boundary with a readable error, instead of surfacing later as an
``IntegrityError`` at flush time. Both layers are intentional: a row can still
arrive from a ``COPY``, a raw script or a future worker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, field_validator

from quantforge.models.base import COUNTRY_LENGTH, CURRENCY_LENGTH, MIC_LENGTH

__all__ = [
    "AVAILABLE_TIMEZONES",
    "CodeValidatingSchema",
    "OrmReadSchema",
    "as_utc_aware",
    "blank_to_none",
]

#: IANA zone names this deployment can actually resolve. A name absent here would
#: fail later at offset arithmetic, so it is rejected at the boundary instead.
AVAILABLE_TIMEZONES: frozenset[str] = frozenset(available_timezones())


def blank_to_none(value: str | None) -> str | None:
    """Collapse an empty or whitespace-only string to ``None``.

    Sources routinely send ``""`` for "not disclosed". Storing ``""`` in a
    nullable code column would satisfy a length constraint while making the row
    behave as populated, so the emptiness is normalised away at the boundary.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class OrmReadSchema(BaseModel):
    """Base for read schemas hydrated from ORM instances.

    ``from_attributes`` allows ``ModelRead.model_validate(orm_instance)``.
    Pydantic never hands an ORM object back out, so the persistence model and
    the wire contract remain separable.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)


class CodeValidatingSchema(BaseModel):
    """Reusable validators for the domain's coded fields.

    Inherited by any schema that declares ``country_code``, ``currency``,
    ``mic`` or ``timezone``. Pydantic applies inherited ``field_validator``
    methods to the subclass's matching fields, which keeps one definition of
    "what a valid country code is" instead of copying it per model.
    """

    @field_validator("country_code", mode="before", check_fields=False)
    @classmethod
    def _check_country_code(cls, value: str | None) -> str | None:
        """Validate an ISO 3166-1 alpha-2 country code."""
        normalised = blank_to_none(value)
        if normalised is None:
            return None
        valid = (
            len(normalised) == COUNTRY_LENGTH
            and normalised.isalpha()
            and normalised.isascii()
            and normalised.isupper()
        )
        if not valid:
            message = (
                "country_code must be an ISO 3166-1 alpha-2 code of exactly "
                f"{COUNTRY_LENGTH} uppercase ASCII letters, got {value!r}."
            )
            raise ValueError(message)
        return normalised

    @field_validator("currency", mode="before", check_fields=False)
    @classmethod
    def _check_currency(cls, value: str | None) -> str | None:
        """Validate an ISO 4217 alphabetic currency code."""
        normalised = blank_to_none(value)
        if normalised is None:
            return None
        valid = (
            len(normalised) == CURRENCY_LENGTH
            and normalised.isalpha()
            and normalised.isascii()
            and normalised.isupper()
        )
        if not valid:
            message = (
                "currency must be an ISO 4217 alphabetic code of exactly "
                f"{CURRENCY_LENGTH} uppercase ASCII letters, got {value!r}."
            )
            raise ValueError(message)
        return normalised

    @field_validator("mic", mode="before", check_fields=False)
    @classmethod
    def _check_mic(cls, value: str) -> str:
        """Validate an ISO 10383 Market Identifier Code."""
        normalised = blank_to_none(value)
        if normalised is None:
            message = "mic must not be blank."
            raise ValueError(message)
        valid = (
            len(normalised) == MIC_LENGTH
            and normalised.isalnum()
            and normalised.isascii()
            and normalised.isupper()
        )
        if not valid:
            message = (
                "mic must be an ISO 10383 code of exactly "
                f"{MIC_LENGTH} uppercase alphanumeric characters, got {value!r}."
            )
            raise ValueError(message)
        return normalised

    @field_validator("timezone", mode="before", check_fields=False)
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        """Validate an IANA timezone name, rejecting fixed UTC offsets.

        ``"-05:00"`` is accepted by the tz database as a valid *input* to
        :func:`datetime.astimezone`, which is exactly why it must be rejected
        here: storing an offset would be wrong for half of every year.
        """
        normalised = blank_to_none(value)
        if normalised is None:
            message = "timezone must not be blank; expected an IANA name like 'America/New_York'."
            raise ValueError(message)
        if normalised not in AVAILABLE_TIMEZONES:
            message = (
                f"timezone {normalised!r} is not a known IANA zone name. Use an IANA "
                "identifier such as 'America/New_York', never a fixed UTC offset."
            )
            raise ValueError(message)
        return normalised


def as_utc_aware(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime, rejecting naive input.

    A naive datetime handed to PostgreSQL's ``TIMESTAMPTZ`` is interpreted in the
    *session's* time zone, so the stored instant would silently depend on
    deployment configuration and on daylight-saving rules. Rejecting naive input
    is the only way to make "this column holds an absolute instant" true.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        message = (
            "timestamps must be timezone-aware; got a naive datetime. "
            "Use datetime(..., tzinfo=UTC) or datetime.now(UTC)."
        )
        raise ValueError(message)
    return value.astimezone(UTC)
