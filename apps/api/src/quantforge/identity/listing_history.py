"""Derive a security's ticker eras from sourced facts and provisional listings (pure, no I/O).

Rules (all deterministic; nothing depends on the clock, row order or ingestion order):

* ``TICKER_STARTED`` facts of ONE source form a chain: each ticker's era runs from its start date
  (inclusive) to the next start date (exclusive). Nothing is claimed before the first start.
* An era's END is proven only by (a) the next start, (b) a ``LAST_TRADED`` fact (inclusive, so the
  exclusive end is the next day), or (c) for the last era, corroboration that the ticker is still
  current (an open provisional listing with the same ticker). A last era with none of those covers
  only up to its latest dated observation (at least its start day); the rest is NOT claimed.
* ``TICKER_OBSERVED`` facts prove exactly their own date: an observation inside an era of the same
  ticker adds nothing; inside an era of a DIFFERENT ticker it is a conflict; outside every era it
  stands alone as a one-day era. Two observations never become a continuous interval.
* A gap between proven eras is never filled.
* Sources never outrank one another. Different sources' eras that overlap with different tickers,
  or with the same ticker but different boundaries, are an explicit CONFLICT over the disagreeing
  dates -- no record is silently chosen.
* A provisional listing is an operational claim. Where it overlaps a sourced era of a DIFFERENT
  ticker the dates are a CONFLICT (venue history is unknown, so a second simultaneous ticker for
  one share class cannot be told apart from a contradiction).

``TICKER_STARTED`` is taken at the provider's word (an inclusive start). Whether the provider's
event date really means that is UNVERIFIED (see the Massive mapper); this module does not pretend
otherwise -- it only derives from whatever the source states, and a source reinterpretation changes the
adapter, not this derivation.

Exchange/MIC continuity is never inferred: a ticker change does not imply the same venue.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from quantforge.models.enums import ListingFactKind

__all__ = [
    "Conflict",
    "Era",
    "Evidence",
    "FactView",
    "ProvisionalListingView",
    "ResolutionStatus",
    "SecurityHistory",
    "derive_security_history",
]

_ONE_DAY = timedelta(days=1)


class Evidence(StrEnum):
    """How a resolved identity is proven. Not a score: two kinds, never blended."""

    #: A ``Listing`` row's first-observed interval (operational knowledge).
    PROVISIONAL_FIRST_OBSERVED = "PROVISIONAL_FIRST_OBSERVED"
    #: Provider-stated history (facts), possibly corroborated by a listing.
    SOURCE_STATED_HISTORY = "SOURCE_STATED_HISTORY"


class ResolutionStatus(StrEnum):
    """The minimal outcome set every resolver query returns."""

    RESOLVED = "RESOLVED"
    #: No evidence of any kind covers the question. Never filled with a guess.
    NO_EVIDENCE = "NO_EVIDENCE"
    #: More than one security, ticker or listing is validly claimed; the caller must disambiguate.
    AMBIGUOUS = "AMBIGUOUS"
    #: The evidence contradicts itself on this date.
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class FactView:
    source_id: uuid.UUID
    kind: ListingFactKind
    ticker: str
    effective_date: date
    mic: str | None = None


@dataclass(frozen=True, slots=True)
class ProvisionalListingView:
    listing_id: uuid.UUID
    ticker: str
    mic: str | None
    valid_from: date
    valid_to: date | None  # exclusive; None = open
    is_primary: bool


@dataclass(frozen=True, slots=True)
class Era:
    """``ticker`` for ``[start, end)``; ``end is None`` means open-ended."""

    ticker: str
    start: date
    end: date | None
    evidence: Evidence
    #: True for a one-day era made from a single dated observation. Such an era corroborates
    #: another source's interval of the same ticker; it never disputes its boundaries.
    point: bool = False
    #: False when ``end`` is not stated by the source: an unclosed last era is only a lower bound
    #: (it runs to its latest observation, or on while a listing corroborates it), so it is never
    #: diffed against another source's stated end.
    end_proven: bool = True

    def covers(self, on: date) -> bool:
        return self.start <= on and (self.end is None or on < self.end)


@dataclass(frozen=True, slots=True)
class Conflict:
    """Dates ``[start, end)`` on which the evidence contradicts itself."""

    start: date
    end: date | None
    reason: str
    #: Tickers whose evidence is in dispute, so a query about one of them sees the conflict even
    #: when the contradicted observation never became an era.
    tickers: frozenset[str] = frozenset()

    def covers(self, on: date) -> bool:
        return self.start <= on and (self.end is None or on < self.end)

    def overlaps(self, start: date, end_exclusive: date | None) -> bool:
        return (self.end is None or self.end > start) and (
            end_exclusive is None or self.start < end_exclusive
        )


@dataclass(frozen=True, slots=True)
class SecurityHistory:
    """Everything proven (and contradicted) about one security's tickers."""

    security_id: uuid.UUID
    sourced_eras: tuple[Era, ...]
    provisional_eras: tuple[Era, ...]
    conflicts: tuple[Conflict, ...]
    listings: tuple[ProvisionalListingView, ...]

    def sourced_tickers_on(self, on: date) -> frozenset[str]:
        return frozenset(era.ticker for era in self.sourced_eras if era.covers(on))

    def provisional_tickers_on(self, on: date) -> frozenset[str]:
        return frozenset(era.ticker for era in self.provisional_eras if era.covers(on))

    def conflict_for_ticker_in(
        self, ticker: str, start: date, end_inclusive: date
    ) -> Conflict | None:
        """A conflict inside ``[start, end_inclusive]`` that concerns ``ticker`` (see below)."""
        stop = end_inclusive + _ONE_DAY
        eras = [era for era in (*self.sourced_eras, *self.provisional_eras) if era.ticker == ticker]
        for conflict in self.conflicts:
            if not conflict.overlaps(start, stop):
                continue
            touches_era = any(
                (era.end is None or conflict.start < era.end)
                and (conflict.end is None or era.start < conflict.end)
                for era in eras
            )
            if ticker in conflict.tickers or touches_era:
                return conflict
        return None

    def conflict_on(self, on: date) -> Conflict | None:
        return next((c for c in self.conflicts if c.covers(on)), None)

    def conflict_in(self, start: date, end_inclusive: date) -> Conflict | None:
        stop = end_inclusive + _ONE_DAY
        return next((c for c in self.conflicts if c.overlaps(start, stop)), None)

    def conflict_for_ticker_on(self, ticker: str, on: date) -> Conflict | None:
        """A conflict on ``on`` that concerns ``ticker``.

        A conflict names the tickers in dispute, but a conflict that overlaps ANY era of ``ticker``
        is treated as concerning it too: the relationship between an era and a disputed date cannot
        be told apart, so the security-level block errs on the side of refusing.
        """
        has_era = any(
            era.ticker == ticker and era.covers(on)
            for era in (*self.sourced_eras, *self.provisional_eras)
        )
        return next(
            (c for c in self.conflicts if c.covers(on) and (has_era or ticker in c.tickers)),
            None,
        )

    def conflict_over_ranges(self, ranges: Sequence[tuple[date, date]]) -> Conflict | None:
        """The first conflict overlapping any of the inclusive ``(from, to)`` ranges."""
        for first, last in ranges:
            conflict = self.conflict_in(first, last)
            if conflict is not None:
                return conflict
        return None

    def covered_ranges(
        self, ticker: str, start: date, end_inclusive: date
    ) -> list[tuple[date, date]]:
        """Inclusive ``(from, to)`` ranges within ``[start, end_inclusive]`` where ``ticker`` is proven.

        Sourced and provisional eras are unioned (adjacent ranges merge); a gap stays a gap.
        """
        spans: list[tuple[date, date]] = []
        for era in (*self.sourced_eras, *self.provisional_eras):
            if era.ticker != ticker:
                continue
            first = max(era.start, start)
            last = end_inclusive if era.end is None else min(era.end - _ONE_DAY, end_inclusive)
            if first <= last:
                spans.append((first, last))
        return _merge_inclusive(spans)


def _merge_inclusive(spans: Iterable[tuple[date, date]]) -> list[tuple[date, date]]:
    merged: list[tuple[date, date]] = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + _ONE_DAY:
            if last > merged[-1][1]:
                merged[-1] = (merged[-1][0], last)
        else:
            merged.append((first, last))
    return merged


def _overlap(a: Era, b: Era) -> tuple[date, date | None] | None:
    start = max(a.start, b.start)
    if a.end is None:
        end = b.end
    elif b.end is None:
        end = a.end
    else:
        end = min(a.end, b.end)
    if end is not None and end <= start:
        return None
    return start, end


def _symmetric_difference(a: Era, b: Era) -> list[tuple[date, date | None]]:
    """Dates covered by exactly one of two overlapping same-ticker eras.

    A start that differs is a disagreement. An END that differs is one only when BOTH sources
    stated it: an unclosed last era is a lower bound, and a source that merely knows less must not
    manufacture a conflict against one that states an end.
    """
    pieces: list[tuple[date, date | None]] = []
    first, second = sorted((a.start, b.start))
    if first != second:
        pieces.append((first, second))
    if a.end != b.end and a.end_proven and b.end_proven:
        # A proven end is always a date (``end_proven`` is set only from a next start or a
        # last-traded fact), never open-ended.
        assert a.end is not None and b.end is not None
        low, high = sorted((a.end, b.end))
        pieces.append((low, high))
    return pieces


def _observation_conflict(
    fact: FactView,
    covering: Sequence[Era],
    started: Sequence[FactView],
    last_traded: Sequence[FactView],
    tails: Sequence[tuple[str, date]],
) -> str | None:
    """Why this observation contradicts the other facts (any source), or ``None`` if it does not."""
    day = fact.effective_date
    if covering:
        return (
            f"observed {fact.ticker} but the ticker events give {covering[0].ticker} on this date"
        )
    ended = any(
        f.ticker == fact.ticker
        and f.effective_date < day
        and not any(
            s.ticker == fact.ticker and f.effective_date < s.effective_date <= day for s in started
        )
        for f in last_traded
    )
    if ended:
        return f"observed {fact.ticker} after its stated last traded date"
    for ticker, since in tails:
        if fact.ticker != ticker and day >= since:
            return (
                f"observed {fact.ticker} after the ticker events made {ticker} current "
                f"on {since} with nothing ending it"
            )
    return None


def _apply_observations(
    eras: list[Era],
    observed: Sequence[FactView],
    started: Sequence[FactView],
    last_traded: Sequence[FactView],
    tails: Sequence[tuple[str, date]],
) -> list[Conflict]:
    """Fold dated observations into ``eras`` (in place): contradictions are returned, strays stand alone."""
    conflicts: list[Conflict] = []
    tickers_by_day: dict[date, set[str]] = defaultdict(set)
    for fact in observed:
        tickers_by_day[fact.effective_date].add(fact.ticker)
    split_days = {day for day, tickers in tickers_by_day.items() if len(tickers) > 1}
    for day in sorted(split_days):
        conflicts.append(
            Conflict(
                day,
                day + _ONE_DAY,
                f"one source observes {sorted(tickers_by_day[day])} on the same date",
                frozenset(tickers_by_day[day]),
            )
        )
    for fact in sorted(observed, key=lambda f: (f.effective_date, f.ticker)):
        day = fact.effective_date
        if day in split_days:
            continue
        covering = [era for era in eras if era.covers(day)]
        if any(era.ticker == fact.ticker for era in covering):
            continue
        reason = _observation_conflict(fact, covering, started, last_traded, tails)
        if reason is not None:
            tickers = frozenset({fact.ticker, *(era.ticker for era in covering)})
            conflicts.append(Conflict(day, day + _ONE_DAY, reason, tickers))
            continue
        eras.append(
            Era(fact.ticker, day, day + _ONE_DAY, Evidence.SOURCE_STATED_HISTORY, point=True)
        )
    return conflicts


def _unclosed_end(
    ticker: str,
    start: date,
    observed: Sequence[FactView],
    open_current: set[str],
    all_facts: Sequence[FactView],
) -> date | None:
    """The exclusive end of a source's last era that nothing in THAT source closes.

    Open-ended only while a first-observed listing corroborates the ticker; otherwise it runs to
    its latest dated observation (at least its start day). Another source's stated last traded
    date caps it either way, so an unclosed era is never stretched past a stated end.
    """
    end: date | None = None
    if ticker not in open_current:
        seen = [
            f.effective_date for f in observed if f.ticker == ticker and f.effective_date >= start
        ]
        end = max(start, *seen) + _ONE_DAY if seen else start + _ONE_DAY
    stated = [
        f.effective_date + _ONE_DAY
        for f in all_facts
        if f.kind is ListingFactKind.LAST_TRADED
        and f.ticker == ticker
        and f.effective_date >= start
    ]
    if stated:
        return min(stated) if end is None else min([end, *stated])
    return end


def _eras_for_one_source(
    facts: Sequence[FactView],
    listings: Sequence[ProvisionalListingView],
    all_facts: Sequence[FactView],
) -> tuple[list[Era], list[Conflict], tuple[str, date] | None]:
    """One source's eras and conflicts, plus its OPEN TAIL: the ``(ticker, start)`` of a last era
    nothing closes (no last-traded fact). The source implies that ticker is still current."""
    conflicts: list[Conflict] = []
    started = sorted(
        (f for f in facts if f.kind is ListingFactKind.TICKER_STARTED),
        key=lambda f: (f.effective_date, f.ticker),
    )
    by_date: dict[date, set[str]] = defaultdict(set)
    for fact in started:
        by_date[fact.effective_date].add(fact.ticker)
    for day, tickers in sorted(by_date.items()):
        if len(tickers) > 1:
            conflicts.append(
                Conflict(
                    day,
                    day + _ONE_DAY,
                    f"one source starts {sorted(tickers)} on the same date",
                    frozenset(tickers),
                )
            )
    last_traded = [f for f in facts if f.kind is ListingFactKind.LAST_TRADED]
    chain: list[tuple[date, str]] = []
    for day, tickers in sorted(by_date.items()):
        if len(tickers) != 1:
            continue
        ticker = next(iter(tickers))
        # A repeated start of the same ticker is the same era -- unless the ticker last traded in
        # between, in which case it is a relisting and a new era.
        if chain and chain[-1][1] == ticker:
            traded_between = any(
                f.ticker == ticker and chain[-1][0] <= f.effective_date < day for f in last_traded
            )
            if not traded_between:
                continue
        chain.append((day, ticker))

    observed = [f for f in facts if f.kind is ListingFactKind.TICKER_OBSERVED]
    open_current = {listing.ticker for listing in listings if listing.valid_to is None}

    eras: list[Era] = []
    tail: tuple[str, date] | None = None
    for index, (start, ticker) in enumerate(chain):
        following = chain[index + 1][0] if index + 1 < len(chain) else None
        end: date | None = following
        traded = [
            f.effective_date
            for f in last_traded
            if f.ticker == ticker
            and f.effective_date >= start
            and (following is None or f.effective_date < following)
        ]
        if traded:
            proven_end = max(traded) + _ONE_DAY
            end = proven_end if following is None else min(following, proven_end)
        elif following is None and not any(
            f.ticker == ticker and f.effective_date >= start
            for f in all_facts
            if f.kind is ListingFactKind.LAST_TRADED
        ):
            tail = (ticker, start)
        if not traded and following is None:
            end = _unclosed_end(ticker, start, observed, open_current, all_facts)
        eras.append(
            Era(
                ticker,
                start,
                end,
                Evidence.SOURCE_STATED_HISTORY,
                end_proven=bool(traded) or following is not None,
            )
        )

    return eras, conflicts, tail


def _merge_same_ticker(eras: Sequence[Era]) -> list[Era]:
    merged: list[Era] = []
    for era in sorted(eras, key=lambda e: (e.ticker, e.start, e.end or date.max)):
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.ticker == era.ticker
            and (previous.end is None or era.start <= previous.end)
        ):
            end = None if previous.end is None or era.end is None else max(previous.end, era.end)
            merged[-1] = Era(
                era.ticker,
                previous.start,
                end,
                era.evidence,
                point=previous.point and era.point,
                end_proven=previous.end_proven and era.end_proven,
            )
        else:
            merged.append(era)
    return sorted(merged, key=lambda e: (e.start, e.ticker))


def _cross_source_conflicts(per_source: Sequence[Sequence[Era]]) -> list[Conflict]:
    """Different sources never outrank one another: any disagreement is an explicit conflict."""
    found: list[Conflict] = []
    for left_index, left in enumerate(per_source):
        for right in per_source[left_index + 1 :]:
            for a in left:
                for b in right:
                    shared = _overlap(a, b)
                    if shared is None:
                        continue
                    if a.ticker != b.ticker:
                        reason = f"sources disagree: {a.ticker} vs {b.ticker}"
                        found.append(
                            Conflict(shared[0], shared[1], reason, frozenset({a.ticker, b.ticker}))
                        )
                        continue
                    if a.point or b.point:
                        continue  # an observation inside another source's era is corroboration
                    reason = f"sources disagree on the boundaries of {a.ticker}"
                    found.extend(
                        Conflict(first, last, reason, frozenset({a.ticker}))
                        for first, last in _symmetric_difference(a, b)
                    )
    return found


def _provisional_conflicts(provisional: Sequence[Era], sourced: Sequence[Era]) -> list[Conflict]:
    found: list[Conflict] = []
    for era in provisional:
        for other in sourced:
            if other.ticker == era.ticker:
                continue
            shared = _overlap(era, other)
            if shared is not None:
                reason = (
                    f"a first-observed listing says {era.ticker} but source history "
                    f"says {other.ticker}"
                )
                found.append(
                    Conflict(shared[0], shared[1], reason, frozenset({era.ticker, other.ticker}))
                )
    return found


def _tail_conflicts(
    provisional: Sequence[Era], tails: Sequence[tuple[str, date]]
) -> list[Conflict]:
    """A first-observed listing under a DIFFERENT ticker after a source's still-open last era.

    The source's latest ticker event says ``ticker`` took over and nothing ended it, yet a listing
    was first observed under another symbol after that: one of them is wrong, and which cannot be
    told, so the dates are a conflict.
    """
    found: list[Conflict] = []
    for ticker, start in tails:
        for era in provisional:
            if era.ticker == ticker:
                continue
            first = max(era.start, start)
            if era.end is not None and era.end <= first:
                continue
            reason = (
                f"source history says {ticker} is current since {start}, but a first-observed "
                f"listing says {era.ticker}"
            )
            found.append(Conflict(first, era.end, reason, frozenset({ticker, era.ticker})))
    return found


def derive_security_history(
    security_id: uuid.UUID,
    facts: Sequence[FactView],
    listings: Sequence[ProvisionalListingView],
) -> SecurityHistory:
    """Derive ``security_id``'s eras and conflicts. Pure: the same inputs always give the same result."""
    by_source: dict[uuid.UUID, list[FactView]] = defaultdict(list)
    for fact in facts:
        by_source[fact.source_id].append(fact)

    per_source: list[list[Era]] = []
    tails: list[tuple[str, date]] = []
    conflicts: list[Conflict] = []
    for source_id in sorted(by_source, key=str):
        eras, found, tail = _eras_for_one_source(by_source[source_id], listings, facts)
        per_source.append(eras)
        conflicts.extend(found)
        if tail is not None:
            tails.append(tail)

    started_all = [f for f in facts if f.kind is ListingFactKind.TICKER_STARTED]
    last_all = [f for f in facts if f.kind is ListingFactKind.LAST_TRADED]
    for index, source_id in enumerate(sorted(by_source, key=str)):
        observed = [f for f in by_source[source_id] if f.kind is ListingFactKind.TICKER_OBSERVED]
        conflicts.extend(
            _apply_observations(per_source[index], observed, started_all, last_all, tails)
        )

    conflicts.extend(_cross_source_conflicts(per_source))
    sourced = _merge_same_ticker([era for eras in per_source for era in eras])
    provisional = [
        Era(
            listing.ticker,
            listing.valid_from,
            listing.valid_to,
            Evidence.PROVISIONAL_FIRST_OBSERVED,
        )
        for listing in listings
    ]
    conflicts.extend(_provisional_conflicts(provisional, sourced))
    conflicts.extend(_tail_conflicts(provisional, tails))

    unique = sorted(set(conflicts), key=lambda c: (c.start, c.end or date.max, c.reason))
    return SecurityHistory(
        security_id=security_id,
        sourced_eras=tuple(sourced),
        provisional_eras=tuple(sorted(provisional, key=lambda e: (e.start, e.ticker))),
        conflicts=tuple(unique),
        listings=tuple(
            sorted(listings, key=lambda li: (li.valid_from, li.ticker, str(li.listing_id)))
        ),
    )
