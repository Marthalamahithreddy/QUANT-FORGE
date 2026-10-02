"""Mapping Massive venue codes to ISO 10383 Market Identifier Codes.

The problem
-----------
``exchange.mic`` in the QuantForge schema is an ISO 10383 MIC, enforced by
``ck_exchange_mic_iso10383`` (four uppercase alphanumerics). A provider's
``primary_exchange`` is *its own* identifier. Sometimes the two coincide; often
the provider uses a code, an acronym or a legacy name. Assuming they are
interchangeable means either rejecting valid listings or, far worse, attaching a
security's prices to the wrong venue -- which quietly corrupts every derived
metric keyed on venue.

The rule
--------
A provider venue code is translated **only** through the explicit table below.
There is no name matching, no prefix guessing, and no "looks like a MIC" test.

An unmapped code is a hard failure (:class:`UnsupportedExchangeError`). That is
deliberate and is the whole point of this module: a loud stop beats a silent
mis-attribution. Supporting a new venue is a one-line, reviewed addition here
rather than a latent data-quality incident discovered during a backtest.

Provenance of each row
----------------------
The MICs in this table are the operating MICs those venues actually publish
under, which is why the provider's code and the MIC are frequently the same
string. The timezone and currency columns are venue facts, not per-security
facts: a listing on NASDAQ and a listing on NYSE share a trading calendar and a
currency, and copying them per instrument would be redundant state that could
drift. Every value is asserted by a unit test, so a typo here fails CI rather
than production.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

__all__ = [
    "UnsupportedExchangeError",
    "VenueIdentity",
    "known_venue_codes",
    "resolve_venue",
    "supported_venue_codes",
]

#: Only US equity venues. Anything else (options, crypto, forex, foreign
#: listings) is out of scope for this milestone and is not mapped, so a
#: non-equity symbol fails loudly instead of being admitted by a fuzzy match.
_US = "US"
_US_EASTERN = "America/New_York"
_US_CENTRAL = "America/Chicago"


@dataclass(frozen=True, slots=True)
class VenueIdentity:
    """Everything QuantForge records about a trading venue.

    ``mic`` is what gets stored; the rest is reference data that keeps the
    exchange row self-describing and lets the session-date conversion use the
    venue's real timezone.
    """

    mic: str
    name: str
    acronym: str
    country_code: str
    timezone: str
    currency: str


class UnsupportedExchangeError(LookupError):
    """The provider named a venue this adapter cannot translate.

    Raised instead of guessing. The message names the offending code so the fix
    is obvious: either the symbol is out of scope, or this table needs a row.
    """


_VENUES: Final[MappingProxyType[str, VenueIdentity]] = MappingProxyType(
    {
        # ── Primary US listings ────────────────────────────────────────────
        "XNAS": VenueIdentity(
            mic="XNAS",
            name="Nasdaq Stock Market",
            acronym="NASDAQ",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        "XNYS": VenueIdentity(
            mic="XNYS",
            name="New York Stock Exchange",
            acronym="NYSE",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        "XASE": VenueIdentity(
            mic="XASE",
            name="NYSE American",
            acronym="AMEX",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        "ARCX": VenueIdentity(
            mic="ARCX",
            name="NYSE Arca",
            acronym="ARCA",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        "BATS": VenueIdentity(
            mic="BATS",
            name="Cboe BZX Exchange",
            acronym="BZX",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        "XCBO": VenueIdentity(
            mic="XCBO",
            name="Cboe Global Markets",
            acronym="CBOE",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        "IEXG": VenueIdentity(
            mic="IEXG",
            name="Investors Exchange",
            acronym="IEX",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        "XBOS": VenueIdentity(
            mic="XBOS",
            name="Nasdaq OMX Boston",
            acronym="BOS",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        "XPHL": VenueIdentity(
            mic="XPHL",
            name="Nasdaq OMX Philadelphia",
            acronym="PHL",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
        # ── OTC ────────────────────────────────────────────────────────────
        "OTCM": VenueIdentity(
            mic="OTCM",
            name="OTC Markets",
            acronym="OTC",
            country_code=_US,
            timezone=_US_EASTERN,
            currency="USD",
        ),
    }
)

#: Provider codes that are themselves valid four-character uppercase MICs. Kept
#: as a documented set so a reader can see at a glance which rows are identity
#: mappings and which are genuine translations. A value here is still routed
#: through :func:`resolve_venue`, never used directly, so a future provider that
#: reuses a MIC-looking string for something else cannot bypass the table.
_IDENTITY_MAPPED_CODES: Final[frozenset[str]] = frozenset(
    code for code in _VENUES if code == _VENUES[code].mic
)


def supported_venue_codes() -> tuple[str, ...]:
    """Every provider venue code this adapter can translate, sorted."""
    return tuple(sorted(_VENUES))


def known_venue_codes() -> frozenset[str]:
    """Provider codes that are identity mappings, i.e. already valid MICs.

    Exposed for the unit test that asserts the table is internally consistent:
    every identity-mapped code must equal its own MIC and match the MIC shape the
    database enforces.
    """
    return _IDENTITY_MAPPED_CODES


def resolve_venue(provider_code: str | None) -> VenueIdentity:
    """Translate a provider venue code into a :class:`VenueIdentity`.

    Args:
        provider_code: The value of the provider's exchange field, in whatever
            spelling the provider uses.

    Returns:
        The mapped venue.

    Raises:
        UnsupportedExchangeError: If the code is absent, blank, or not in the
            table. No fallback and no fuzzy match.
    """
    if provider_code is None or not provider_code.strip():
        message = "provider did not supply a venue for this symbol; refusing to guess an exchange"
        raise UnsupportedExchangeError(message)

    candidate = provider_code.strip().upper()
    venue = _VENUES.get(candidate)
    if venue is None:
        message = (
            f"provider venue code {provider_code!r} has no ISO 10383 mapping in this "
            f"adapter. Supported codes: {', '.join(supported_venue_codes())}. Add an "
            "explicit row to providers/massive/exchanges.py rather than mapping it "
            "by guesswork."
        )
        raise UnsupportedExchangeError(message)
    return venue
