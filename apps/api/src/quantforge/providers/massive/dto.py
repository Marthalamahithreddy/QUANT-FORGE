"""Typed views of the Massive REST payloads this milestone consumes.

These are the only classes in the codebase that know Massive spells a price
``o``/``h``/``l``/``c`` and a timestamp ``t``. They live inside the adapter
directory and are never imported by the ingestion service, the repositories or
the models.

Parsing contract
----------------
All prices are ``Decimal``, and they get there by *construction* rather than by
conversion: the client decodes the response with ``parse_float=Decimal`` (see
:mod:`quantforge.providers.json_decimal`), so a price arrives at this layer
already as an exact ``Decimal`` built from the provider's own digits. No
intermediate ``float`` is created, so ``199.99`` cannot become
``199.99000000000000284``.

Unknown fields are ignored rather than rejected. A provider adding a field is a
normal, non-breaking event, and refusing to ingest because of an unrecognised
key would make the pipeline brittle for no benefit. Conversely, a field whose
*type* is wrong is rejected: silently coercing a string into a number is how bad
data becomes stored data.

Single-letter provider keys are declared once, here, and given readable names
inside the class. Everywhere above the adapter it is ``bar.open``, never
``bar["o"]``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantforge.providers.errors import ProviderPayloadError

__all__ = [
    "MASSIVE_SECURITY_TYPE_BY_CODE",
    "MassiveDailyBarDTO",
    "MassiveTickerDetailsDTO",
]

#: Anchor for converting epoch milliseconds to an aware datetime without ever
#: going through a float.
_UNIX_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)

#: An SEC CIK is fixed-width and zero-padded to this many digits. Providers emit
#: it inconsistently ("320193" and "0000320193" are the same registrant), so it is
#: normalised once here rather than at each use site.
_CIK_LENGTH: Final[int] = 10

#: Massive's instrument-class codes, limited to the ones this milestone
#: understands. An unrecognised code is a hard failure rather than a guess: this
#: milestone is US common and preferred equity, and mapping an unknown code onto
#: ``OTHER`` would quietly put an ETF or a warrant into an equity pipeline.
MASSIVE_SECURITY_TYPE_BY_CODE: Final[dict[str, str]] = {
    "CS": "EQUITY",
    "PS": "PREFERRED_EQUITY",
}


class _MassiveModel(BaseModel):
    """Shared configuration for provider DTOs."""

    model_config = ConfigDict(
        # Tolerate a provider adding fields; never coerce a wrong type silently.
        extra="ignore",
        frozen=True,
        # A string that is not a number must not be quietly parsed into one.
        strict=False,
        populate_by_name=True,
    )


class MassiveTickerDetailsDTO(_MassiveModel):
    """``GET /v3/reference/tickers/{ticker}`` -- the ``results`` object.

    Only fields QuantForge can use are declared. A field that is absent from the
    response becomes ``None``, which is the honest reading of "the provider did
    not supply this" and is materially different from ``""`` or ``0``.
    """

    ticker: str
    name: str | None = None
    market: str | None = None
    locale: str | None = None

    # ── Provider-specific keys, canonical attribute names ──────────────────
    # The alias is the provider's spelling and the only place it appears; the
    # attribute is the provider-independent view, which is what lets this DTO
    # satisfy TickerReferenceView without inheriting from it.
    #: Massive's ``primary_exchange``.
    provider_exchange_code: str | None = Field(default=None, alias="primary_exchange")
    #: Massive's ``type``, its instrument-class code.
    security_type_code: str | None = Field(default=None, alias="type")
    #: Massive's ``currency_name``.
    currency_code: str | None = Field(default=None, alias="currency_name")

    #: Massive's ``active``.
    is_active: bool | None = Field(default=None, alias="active")
    cik: str | None = None
    composite_figi: str | None = None
    share_class_figi: str | None = None
    description: str | None = None

    @field_validator("ticker")
    @classmethod
    def _require_non_blank_ticker(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            message = "provider returned a blank ticker"
            raise ValueError(message)
        return stripped.upper()

    @field_validator("cik")
    @classmethod
    def _normalise_cik(cls, value: str | None) -> str | None:
        """Zero-pad a CIK to its canonical 10-digit form.

        SEC CIKs are fixed-width 10 digits. Providers emit them inconsistently
        -- ``320193`` and ``0000320193`` are the same issuer -- so normalising
        here is what stops the same company being ingested as two identities.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not stripped.isdigit():
            message = f"CIK {value!r} is not numeric"
            raise ValueError(message)
        if len(stripped) > _CIK_LENGTH:
            message = f"CIK {value!r} is longer than {_CIK_LENGTH} digits"
            raise ValueError(message)
        return stripped.zfill(10)

    @field_validator("composite_figi", "share_class_figi")
    @classmethod
    def _blank_figi_is_absent(cls, value: str | None) -> str | None:
        """Treat an empty FIGI as absent rather than storing an empty string.

        A FIGI is a globally unique identifier, so ``""`` is never a valid one.
        Storing it would violate the identifier's own uniqueness contract and
        would collide with every other empty FIGI.
        """
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("currency_code")
    @classmethod
    def _blank_currency_is_absent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip().upper()
        return stripped or None

    @property
    def is_us_listed(self) -> bool:
        """Whether this is a US symbol, judged on ``locale``.

        ``market`` is not used for this: it holds asset classes as well as
        regions (``stocks``, ``crypto``, ``forex``), so locale is the field that
        actually answers "is this a US listing".
        """
        return (self.locale or "").strip().lower() == "us"


class MassiveDailyBarDTO(_MassiveModel):
    """One entry from a daily aggregate response's ``results`` array.

    The single-letter keys are renamed on the way in. Nothing above this class
    ever sees ``o``, ``h``, ``l``, ``c``, ``v`` or ``t``.
    """

    open: Decimal = Field(alias="o")
    high: Decimal = Field(alias="h")
    low: Decimal = Field(alias="l")
    close: Decimal = Field(alias="c")
    volume: int | None = Field(default=None, alias="v")
    #: Epoch milliseconds. Converted once, in the mapper, to an aware datetime.
    #: The raw integer is kept so the conversion stays inspectable and testable.
    #: The live provider keys this ``t`` (lowercase), matching its aggregate
    #: schema; the earliest fixtures guessed ``T`` and were wrong.
    timestamp_ms: int = Field(alias="t")
    #: Volume-weighted average price, when the provider includes it. Kept
    #: because it is free, already computed, and useful for later liquidity work.
    vwap: Decimal | None = Field(default=None, alias="vw")
    #: Number of trades, when supplied.
    trades: int | None = Field(default=None, alias="n")

    @field_validator("volume", mode="before")
    @classmethod
    def _volume_must_be_integral(cls, value: object) -> object:
        """Return a whole-share volume, normalising provider float artifacts.

        The live provider publishes whole-share volume as a number that can
        carry a fractional tail -- a server-side float-summation artefact, e.g.
        ``39269147.943173``. The schema stores volume as ``BIGINT`` ("shares
        or lots, as reported"), so a fractional figure is rounded to the
        nearest whole share, which is the closest whole count to what the
        provider reported. A value that is already integral passes through
        unchanged.

        Preservation of the earlier rule: a *type* that is not a number --
        a symbol string, a dict, ``None`` -- is still rejected rather than
        coerced. ``Decimal`` is the only fractional type that can reach here
        from the provider, because the client decodes floats as decimals; a
        ``float`` is accepted only through a non-HTTP transport.
        """
        if value is None or isinstance(value, int):
            return value
        if isinstance(value, Decimal):
            if value != value.to_integral_value():
                value = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
            return int(value)
        if isinstance(value, float):
            # Reached only if a caller bypasses decode_json_document, e.g. a
            # test or a future non-HTTP transport. Same policy as Decimal.
            value = Decimal(str(value))
            if value != value.to_integral_value():
                value = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
            return int(value)
        message = f"volume has unusable type {type(value).__name__}"
        raise ValueError(message)

    @property
    def session_instant(self) -> datetime:
        """The aggregate's timestamp as an aware UTC datetime.

        Computed as ``epoch + timedelta(milliseconds=...)``: pure integer
        arithmetic, with no float anywhere in the conversion.

        The more obvious ``fromtimestamp(ms / 1000)`` was measured to give the
        same answer for every millisecond value in 1990-2100, because
        ``fromtimestamp`` rounds to the microsecond and the rounding happens to
        land correctly at these magnitudes. So this is not a fix for an
        observable bug -- it removes the dependence on that. A conversion whose
        correctness rests on a rounding step agreeing to the microsecond is one
        refactor away from being quietly wrong, and there is no reason to accept
        that when integer arithmetic is no less readable.

        The returned value is an *instant*, not a session date -- the mapper
        converts it through the venue's timezone.
        """
        return _UNIX_EPOCH + timedelta(milliseconds=self.timestamp_ms)

    @classmethod
    def from_provider(cls, payload: Any, *, identifier: str | None = None) -> MassiveDailyBarDTO:
        """Build a DTO from one decoded ``results`` element.

        The identifier is only used to make a validation failure name the
        offending record, since a bare "field required" on a 5000-element page
        is not actionable.
        """
        if not isinstance(payload, dict):
            message = f"expected an object, got {type(payload).__name__}"
            raise ProviderPayloadError(message, identifier=identifier)
        try:
            return cls.model_validate(payload)
        except Exception as exc:
            message = f"invalid daily aggregate: {exc}"
            raise ProviderPayloadError(message, identifier=identifier) from exc
