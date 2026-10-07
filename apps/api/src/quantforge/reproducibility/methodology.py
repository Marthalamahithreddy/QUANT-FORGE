"""Explicit methodology version labels (Step 10.9A).

Each label names the SEMANTICS of one computation that can change a result. They are deliberate,
hand-maintained constants: changing what a component computes REQUIRES bumping its label (and the
tests pin the table so a drive-by edit is visible). They are never derived from a Git SHA, a package
version, a source hash or the date.

What is and is not versioned (honest scope):

* The session sequence comes from the exchange calendar (``exchange_calendars``), NOT from stored
  bars (Step 11.2E; before it, "stored-bar-sessions/1" let a missing bar shift an anchor or a
  horizon). The calendar is an external dataset: its content changes with the library version, so
  the label ``exchange-calendar-sessions/1`` is tied to ``EXCHANGE_CALENDARS_VERSION`` by a test --
  upgrading the library fails that test until the label is bumped deliberately. The stored bars
  actually consumed (anchor and target closes) are pinned as evidence, not as methodology.
* The point-in-time policy is versioned together with the set of approved availability bases,
  read from the policy itself, so approving a basis changes the methodology identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from quantforge.market.sessions import SESSION_CALENDAR_RELEASE
from quantforge.models.enums import AvailabilityBasis
from quantforge.research.alignment import ALIGNMENT_WINDOW_DAYS
from quantforge.research.availability import APPROVED_AVAILABILITY_BASES

__all__ = [
    "COMPONENT_VERSIONS",
    "CURRENT_METHODOLOGY",
    "EXCHANGE_CALENDARS_VERSION",
    "MethodologyIdentity",
    "current_methodology",
]

#: The ``exchange_calendars`` release the ``session_alignment`` label vouches for (pinned by a test).
EXCHANGE_CALENDARS_VERSION: Final = SESSION_CALENDAR_RELEASE

#: component -> version label. Bump deliberately when the component's semantics change.
COMPONENT_VERSIONS: Final[dict[str, str]] = {
    "observation_contract": "research-observation/1",
    "event_study": "event-study/1",
    "session_alignment": "exchange-calendar-sessions/1",
    "price_series": "coherent-single-source-series/1",
    "forward_returns": "close-to-close-proven-session-returns/1",
    "benchmark_returns": "matched-endpoint-benchmark/1",
    "abnormal_returns": "market-adjusted-simple/1",
    "listing_resolution": "listing-resolution/1",
    "availability_policy": "availability-policy/1",
    "eps_surprise": "eps-surprise/1",
    "filing_eps_selection": "filing-eps-selection/1",
    "eps_change": "eps-change/1",
    "manifest_schema": "reproducibility-manifest/2",
}


@dataclass(frozen=True, slots=True)
class MethodologyIdentity:
    """The methodology a computation ran under: labels plus the policy parameters they rely on."""

    components: tuple[tuple[str, str], ...]
    approved_availability_bases: tuple[AvailabilityBasis, ...]
    alignment_window_days: int

    def __post_init__(self) -> None:
        if list(self.components) != sorted(self.components):
            message = "methodology components must be sorted by name"
            raise ValueError(message)
        if list(self.approved_availability_bases) != sorted(self.approved_availability_bases):
            message = "approved availability bases must be sorted"
            raise ValueError(message)


def current_methodology(
    overrides: dict[str, str] | None = None,
) -> MethodologyIdentity:
    """The methodology in force now; ``overrides`` exist so tests can simulate a version bump."""
    unknown = set(overrides or {}) - set(COMPONENT_VERSIONS)
    if unknown:
        message = f"unknown methodology component(s): {sorted(unknown)}"
        raise ValueError(message)
    versions = {**COMPONENT_VERSIONS, **(overrides or {})}
    return MethodologyIdentity(
        components=tuple(sorted(versions.items())),
        approved_availability_bases=tuple(sorted(APPROVED_AVAILABILITY_BASES)),
        alignment_window_days=ALIGNMENT_WINDOW_DAYS,
    )


CURRENT_METHODOLOGY: Final = current_methodology()
