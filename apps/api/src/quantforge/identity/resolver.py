"""The one historical listing resolver every consumer uses.

Earnings alignment, earnings ingestion, the earnings read API and split ingestion all ask the same
question -- "what did this ticker/company/security mean on this date, and how well is that proven?"
-- and all answer it here, from the same derivation
(:func:`~quantforge.identity.listing_history.derive_security_history`), so they cannot disagree.

Results carry an explicit status (``RESOLVED`` / ``NO_EVIDENCE`` / ``AMBIGUOUS`` / ``CONFLICT``)
and an evidence kind, never a bare ``Listing | None`` and never a confidence score. Resolving a
date that only a first-observed ``Listing`` covers behaves exactly as it always did; sourced
history can extend that backwards, but it can never turn a gap, a conflict or an ambiguity into
continuity.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.identity.listing_history import (
    Evidence,
    FactView,
    ProvisionalListingView,
    ResolutionStatus,
    SecurityHistory,
    derive_security_history,
)
from quantforge.models.enums import IdentifierType
from quantforge.models.listing_history import ListingHistoryFact
from quantforge.models.security import Listing, Security, SecurityIdentifier

__all__ = [
    "CompanyResolution",
    "HistoricalListingResolver",
    "SecurityResolution",
    "TickerEraResolution",
    "TickerResolution",
]

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class SecurityResolution:
    """Which ticker/listing a security had on a date.

    ``RESOLVED`` with ``listing_id is None`` means the ticker is proven but no stored ``Listing``
    row carries it (for example a retired symbol that was never ingested): the identity is known,
    price bars attributed to a listing are not.
    """

    status: ResolutionStatus
    security_id: uuid.UUID
    ticker: str | None
    evidence: Evidence | None
    listing_id: uuid.UUID | None
    exchange_mic: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class CompanyResolution:
    """Which of a company's securities (and listing) applied on a date."""

    status: ResolutionStatus
    security_id: uuid.UUID | None
    ticker: str | None
    evidence: Evidence | None
    listing_id: uuid.UUID | None
    exchange_mic: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class TickerResolution:
    """Which security a ticker meant on a date. ``company_ids`` lists every claimant's issuer."""

    status: ResolutionStatus
    security_id: uuid.UUID | None
    company_id: uuid.UUID | None
    evidence: Evidence | None
    reason: str
    company_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    security_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    #: False only when NO evidence of any kind has ever mentioned this ticker.
    known: bool = True


@dataclass(frozen=True, slots=True)
class TickerEraResolution:
    """The dates in a range on which ONE security is proven to have held a ticker."""

    status: ResolutionStatus
    security_id: uuid.UUID | None
    ranges: tuple[tuple[date, date], ...]
    reason: str
    security_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


def _primary_choice(pool: Sequence[ProvisionalListingView]) -> ProvisionalListingView | None:
    if len(pool) == 1:
        return pool[0]
    primaries = [listing for listing in pool if listing.is_primary]
    return primaries[0] if len(primaries) == 1 else None


def _resolve_security(history: SecurityHistory, on: date) -> SecurityResolution:  # noqa: PLR0911 - one return per distinct, named outcome; splitting it would hide the decision table
    """Pure resolution of one security on one date from its derived history."""
    sid = history.security_id

    def result(  # noqa: PLR0913 - one keyword per result field; a wrapper would add nothing
        status: ResolutionStatus,
        reason: str,
        *,
        ticker: str | None = None,
        evidence: Evidence | None = None,
        listing: ProvisionalListingView | None = None,
        venue_evidenced: bool = True,
    ) -> SecurityResolution:
        # The venue is reported only when a first-observed interval covers the date. A stored
        # listing that merely carries the ticker (first observed years later, perhaps elsewhere)
        # identifies the instrument, not where it traded then: no exchange continuity is inferred.
        return SecurityResolution(
            status=status,
            security_id=sid,
            ticker=ticker,
            evidence=evidence,
            listing_id=None if listing is None else listing.listing_id,
            exchange_mic=None if listing is None or not venue_evidenced else listing.mic,
            reason=reason,
        )

    conflict = history.conflict_on(on)
    if conflict is not None:
        return result(ResolutionStatus.CONFLICT, conflict.reason)

    valid = [
        listing
        for listing in history.listings
        if listing.valid_from <= on and (listing.valid_to is None or on < listing.valid_to)
    ]
    sourced = history.sourced_tickers_on(on)

    if not sourced:
        if not valid:
            return result(ResolutionStatus.NO_EVIDENCE, f"no listing of {sid} is proven on {on}")
        choice = _primary_choice(valid)
        if choice is None:
            return result(
                ResolutionStatus.AMBIGUOUS,
                f"{len(valid)} listings of {sid} were valid on {on} and none is the single primary",
            )
        return result(
            ResolutionStatus.RESOLVED,
            "exactly one first-observed listing was valid on this date",
            ticker=choice.ticker,
            evidence=Evidence.PROVISIONAL_FIRST_OBSERVED,
            listing=choice,
        )

    if len(sourced) > 1:
        return result(
            ResolutionStatus.AMBIGUOUS,
            f"source history gives {len(sourced)} tickers for {sid} on {on}",
        )
    ticker = next(iter(sourced))
    pool = [listing for listing in valid if listing.ticker == ticker] or [
        listing for listing in history.listings if listing.ticker == ticker
    ]
    if not pool:
        return result(
            ResolutionStatus.RESOLVED,
            f"source history proves {ticker} on {on}, but no stored listing carries that ticker",
            ticker=ticker,
            evidence=Evidence.SOURCE_STATED_HISTORY,
        )
    choice = _primary_choice(pool)
    if choice is None:
        return result(
            ResolutionStatus.AMBIGUOUS,
            f"{len(pool)} listings of {sid} carry {ticker} and none is the single primary "
            "(venue history is not established)",
            ticker=ticker,
            evidence=Evidence.SOURCE_STATED_HISTORY,
        )
    venue_known = choice in valid
    return result(
        ResolutionStatus.RESOLVED,
        f"source history proves {ticker} on {on}"
        + ("" if venue_known else "; the venue on that date is not established"),
        ticker=ticker,
        evidence=Evidence.SOURCE_STATED_HISTORY,
        listing=choice,
        venue_evidenced=venue_known,
    )


class HistoricalListingResolver:
    """DB-backed resolver. Read-only: it writes nothing and caches nothing across calls."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ── loading ─────────────────────────────────────────────────────────

    def histories(self, security_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, SecurityHistory]:
        """The derived history of each security (two queries, however many securities)."""
        ids = sorted(set(security_ids), key=str)
        if not ids:
            return {}
        facts: dict[uuid.UUID, list[FactView]] = defaultdict(list)
        for fact in self._session.scalars(
            sa.select(ListingHistoryFact).where(ListingHistoryFact.security_id.in_(ids))
        ):
            facts[fact.security_id].append(
                FactView(
                    source_id=fact.source_id,
                    kind=fact.kind,
                    ticker=fact.ticker,
                    effective_date=fact.effective_date,
                    mic=fact.mic,
                )
            )
        listings: dict[uuid.UUID, list[ProvisionalListingView]] = defaultdict(list)
        for listing in self._session.scalars(
            sa.select(Listing).where(Listing.security_id.in_(ids))
        ):
            listings[listing.security_id].append(
                ProvisionalListingView(
                    listing_id=listing.id,
                    ticker=listing.ticker,
                    mic=listing.exchange.mic,
                    valid_from=listing.valid_from,
                    valid_to=listing.valid_to,
                    is_primary=listing.is_primary,
                )
            )
        return {sid: derive_security_history(sid, facts[sid], listings[sid]) for sid in ids}

    def securities_with_ticker(self, ticker: str) -> set[uuid.UUID]:
        """Every security any evidence (a listing or a sourced fact) ever tied to ``ticker``."""
        symbol = ticker.upper()
        from_listings = self._session.scalars(
            sa.select(Listing.security_id).where(Listing.ticker == symbol)
        )
        from_facts = self._session.scalars(
            sa.select(ListingHistoryFact.security_id).where(ListingHistoryFact.ticker == symbol)
        )
        return {*from_listings, *from_facts}

    def security_id_for_share_class_figi(self, figi: str) -> uuid.UUID | None:
        """The security whose SHARE-CLASS FIGI this is.

        Only ``FIGI_SHARE_CLASS`` identifiers count. A legacy ``FIGI`` row (level unspecified),
        a composite FIGI, a ticker or a CIK never serves as a security anchor.
        """
        return self._session.scalar(
            sa.select(SecurityIdentifier.security_id).where(
                SecurityIdentifier.identifier_type == IdentifierType.FIGI_SHARE_CLASS,
                SecurityIdentifier.identifier_value == figi,
            )
        )

    def _company_ids(self, security_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
        ids = list(security_ids)
        if not ids:
            return {}
        rows = self._session.execute(
            sa.select(Security.id, Security.company_id).where(Security.id.in_(ids))
        )
        return {sid: cid for sid, cid in rows}  # noqa: C416 - Row unpacking; dict(rows) is deprecated

    # ── queries ─────────────────────────────────────────────────────────

    def resolve_security_on(self, security_id: uuid.UUID, on_date: date) -> SecurityResolution:
        """Which ticker/listing ``security_id`` had on ``on_date``."""
        history = self.histories([security_id])[security_id]
        return _resolve_security(history, on_date)

    def resolve_company_on(
        self,
        company_id: uuid.UUID,
        on_date: date,
        *,
        security_id: uuid.UUID | None = None,
    ) -> CompanyResolution:
        """Which of a company's securities (and listing) applied on ``on_date``.

        ``security_id`` narrows the candidates to one of the company's own securities; a security
        of another company is never used.
        """
        query = sa.select(Security.id).where(Security.company_id == company_id)
        if security_id is not None:
            query = query.where(Security.id == security_id)
        candidates = list(self._session.scalars(query))

        def empty(status: ResolutionStatus, reason: str) -> CompanyResolution:
            return CompanyResolution(status, None, None, None, None, None, reason)

        if security_id is not None and not candidates:
            return empty(
                ResolutionStatus.NO_EVIDENCE,
                f"security {security_id} does not belong to company {company_id}",
            )
        resolved: list[SecurityResolution] = []
        for history in self.histories(candidates).values():
            outcome = _resolve_security(history, on_date)
            if outcome.status is ResolutionStatus.CONFLICT:
                return empty(ResolutionStatus.CONFLICT, outcome.reason)
            if outcome.status is ResolutionStatus.AMBIGUOUS:
                return empty(ResolutionStatus.AMBIGUOUS, outcome.reason)
            if outcome.status is ResolutionStatus.RESOLVED:
                resolved.append(outcome)
        if not resolved:
            return empty(
                ResolutionStatus.NO_EVIDENCE,
                f"no listing of company {company_id} is proven on {on_date}",
            )
        if len(resolved) > 1:
            return empty(
                ResolutionStatus.AMBIGUOUS,
                f"{len(resolved)} securities of company {company_id} apply on {on_date}; "
                "pass an explicit security_id to disambiguate",
            )
        only = resolved[0]
        return CompanyResolution(
            status=ResolutionStatus.RESOLVED,
            security_id=only.security_id,
            ticker=only.ticker,
            evidence=only.evidence,
            listing_id=only.listing_id,
            exchange_mic=only.exchange_mic,
            reason=only.reason,
        )

    def resolve_ticker_on(self, ticker: str, on_date: date) -> TickerResolution:
        """Which security ``ticker`` meant on ``on_date``."""
        symbol = ticker.upper()
        in_play = self.securities_with_ticker(symbol)
        if not in_play:
            return TickerResolution(
                ResolutionStatus.NO_EVIDENCE,
                None,
                None,
                None,
                f"{symbol} is not stored",
                known=False,
            )
        histories = self.histories(in_play)
        claimants: dict[uuid.UUID, Evidence] = {}
        for sid, history in histories.items():
            conflict = history.conflict_for_ticker_on(symbol, on_date)
            if conflict is not None:
                return TickerResolution(
                    ResolutionStatus.CONFLICT,
                    None,
                    None,
                    None,
                    f"{symbol} on {on_date}: {conflict.reason}",
                    security_ids=frozenset({sid}),
                )
            if symbol in history.sourced_tickers_on(on_date):
                claimants[sid] = Evidence.SOURCE_STATED_HISTORY
            elif symbol in history.provisional_tickers_on(on_date):
                claimants[sid] = Evidence.PROVISIONAL_FIRST_OBSERVED
        companies = self._company_ids(claimants)
        company_ids = frozenset(companies.values())
        if not claimants:
            return TickerResolution(
                ResolutionStatus.NO_EVIDENCE,
                None,
                None,
                None,
                f"no evidence ties {symbol} to a security on {on_date}",
            )
        if len(claimants) > 1:
            return TickerResolution(
                ResolutionStatus.AMBIGUOUS,
                None,
                None,
                None,
                f"{symbol} is claimed by {len(claimants)} securities on {on_date}",
                company_ids=company_ids,
                security_ids=frozenset(claimants),
            )
        ((sid, evidence),) = claimants.items()
        return TickerResolution(
            ResolutionStatus.RESOLVED,
            sid,
            companies[sid],
            evidence,
            f"{symbol} belonged to this security on {on_date}",
            company_ids=company_ids,
            security_ids=frozenset({sid}),
        )

    def resolve_ticker_eras(self, ticker: str, start: date, end: date) -> TickerEraResolution:
        """Where, inside ``[start, end]``, ONE security is proven to have held ``ticker``.

        More than one security with evidence inside the range is ``AMBIGUOUS`` (a reused ticker:
        narrow the range); a contradiction inside it is ``CONFLICT``; no proven date is
        ``NO_EVIDENCE``. Gaps stay gaps.
        """
        symbol = ticker.upper()
        histories = self.histories(self.securities_with_ticker(symbol))
        with_evidence: dict[uuid.UUID, list[tuple[date, date]]] = {}
        for sid, history in histories.items():
            ranges = history.covered_ranges(symbol, start, end)
            if ranges:
                with_evidence[sid] = ranges
        # A conflict concerning the ticker on ANY security in play blocks the range, exactly as
        # resolve_ticker_on and the API ranges treat it.
        for sid, history in histories.items():
            conflict = history.conflict_for_ticker_in(symbol, start, end)
            if conflict is not None:
                return TickerEraResolution(
                    ResolutionStatus.CONFLICT,
                    sid,
                    (),
                    f"{symbol} {start}..{end}: {conflict.reason}",
                    security_ids=frozenset({sid}),
                )
        if not with_evidence:
            return TickerEraResolution(
                ResolutionStatus.NO_EVIDENCE,
                None,
                (),
                f"no evidence ties {symbol} to any security between {start} and {end}",
            )
        if len(with_evidence) > 1:
            return TickerEraResolution(
                ResolutionStatus.AMBIGUOUS,
                None,
                (),
                f"{symbol} belonged to {len(with_evidence)} different securities between "
                f"{start} and {end} (a reused ticker)",
                security_ids=frozenset(with_evidence),
            )
        ((sid, ranges),) = with_evidence.items()
        return TickerEraResolution(
            ResolutionStatus.RESOLVED,
            sid,
            tuple(ranges),
            "ticker eras proven for one security",
            security_ids=frozenset({sid}),
        )

    # ── helpers for set-oriented consumers (the earnings read API) ──────

    def valid_listing_ids_on(self, history: SecurityHistory, on_date: date) -> frozenset[uuid.UUID]:
        """Stored listings the resolver treats as valid for the security on ``on_date``.

        Every listing whose own first-observed interval covers the date, plus the single listing
        the resolver itself selects when source history proves a ticker no covering listing carries
        (the same ``_resolve_security`` decision, so the two cannot disagree). Several listings
        carrying a sourced ticker with no single primary are an ambiguity: none is flagged.
        """
        if history.conflict_on(on_date) is not None:
            return frozenset()
        ids = {
            listing.listing_id
            for listing in history.listings
            if listing.valid_from <= on_date
            and (listing.valid_to is None or on_date < listing.valid_to)
        }
        chosen = _resolve_security(history, on_date)
        if chosen.status is ResolutionStatus.RESOLVED and chosen.listing_id is not None:
            ids.add(chosen.listing_id)
        return frozenset(ids)

    def ticker_company_ranges(self, ticker: str) -> list[tuple[uuid.UUID, date, date | None]]:
        """``(company_id, start_inclusive, end_exclusive|None)`` ranges where ``ticker`` is proven.

        Conflicted dates are excluded, so a filter built from these ranges agrees with
        :meth:`resolve_ticker_on` about WHICH COMPANY a date belongs to. (Several securities of
        ONE company claiming the ticker are an ambiguity for a security question but not for a
        company question, and earnings attribution accepts them, so those dates stay.) Ranges are merged per company, so the number of
        ranges does not grow with the number of ingested observations.
        """
        symbol = ticker.upper()
        histories = self.histories(self.securities_with_ticker(symbol))
        companies = self._company_ids(histories)
        raw: dict[uuid.UUID, list[tuple[date, date | None]]] = defaultdict(list)
        holes: list[tuple[date, date | None]] = []
        for sid, history in histories.items():
            eras = [
                era
                for era in (*history.sourced_eras, *history.provisional_eras)
                if era.ticker == symbol
            ]
            raw[sid].extend((era.start, era.end) for era in eras)
            # A conflict concerning the ticker blocks EVERY claimant on those dates, exactly as
            # resolve_ticker_on returns CONFLICT before looking at the other securities.
            holes.extend(
                (conflict.start, conflict.end)
                for conflict in history.conflicts
                if symbol in conflict.tickers
                or any(
                    era.start < (conflict.end or date.max)
                    and (era.end is None or conflict.start < era.end)
                    for era in eras
                )
            )
        # Two securities of DIFFERENT companies both claiming the ticker is an ambiguity
        # (resolve_ticker_on says AMBIGUOUS): those dates are excluded too, using the raw claims.
        pieces_by_company: dict[uuid.UUID, list[tuple[date, date | None]]] = defaultdict(list)
        for sid, own in raw.items():
            remaining = list(own)
            for hole in holes:
                remaining = [p for current in remaining for p in _subtract(current, hole)]
            for other_sid, other_pieces in raw.items():
                if companies[other_sid] == companies[sid]:
                    continue
                for hole in other_pieces:
                    remaining = [p for current in remaining for p in _subtract(current, hole)]
            pieces_by_company[companies[sid]].extend(remaining)
        return [
            (company_id, start, end)
            for company_id, pieces in sorted(
                pieces_by_company.items(), key=lambda item: str(item[0])
            )
            for start, end in _merge_half_open(pieces)
        ]


def _merge_half_open(
    spans: Iterable[tuple[date, date | None]],
) -> list[tuple[date, date | None]]:
    merged: list[tuple[date, date | None]] = []
    for start, end in sorted(spans, key=lambda s: (s[0], s[1] or date.max)):
        if merged and (merged[-1][1] is None or start <= merged[-1][1]):
            previous_end = merged[-1][1]
            new_end = None if previous_end is None or end is None else max(previous_end, end)
            merged[-1] = (merged[-1][0], new_end)
        else:
            merged.append((start, end))
    return merged


def _subtract(
    span: tuple[date, date | None], hole: tuple[date, date | None]
) -> list[tuple[date, date | None]]:
    start, end = span
    hole_start, hole_end = hole
    if (hole_end is not None and hole_end <= start) or (end is not None and end <= hole_start):
        return [span]
    pieces: list[tuple[date, date | None]] = []
    if hole_start > start:
        pieces.append((start, hole_start))
    if hole_end is not None and (end is None or hole_end < end):
        pieces.append((hole_end, end))
    return pieces
