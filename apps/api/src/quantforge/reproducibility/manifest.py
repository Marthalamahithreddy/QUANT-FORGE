"""Reproducibility manifest of one :class:`ResearchObservation` (Step 10.9A). Pure model + read-only builder.

Four distinct identities, never collapsed:

* **request** -- the effective inputs after defaults (what was asked);
* **methodology** -- explicit version labels + policy parameters (how it is computed);
* **evidence** -- identifiers/hashes of the stored data the computation actually consumed;
* **result** -- a fingerprint of the computed observation itself (what came out).

The manifest fingerprint covers request + methodology + evidence. It says "same inputs", never "same
output" and never "the database still holds the bytes that existed historically"; the result
fingerprint answers "did the output change". A fingerprint match is NOT proof of complete
reproducibility: that is the separate, evidence-derived :class:`ReproducibilityLevel`.

Evidence is what was CONSUMED, not database state: only the stock/benchmark bars at the sessions the
study used, a digest of the stored session dates in the window it read (a later-added bar there
would change the result, so the dates are consumed evidence), the event/filing/accounting identities
it carried. Unrelated rows never enter the manifest.

Residual assumptions (not pinned, listed in :data:`RESIDUAL_ASSUMPTIONS`, never in the fingerprint):
stored price bars and filings are immutable by convention, and the listing/identity reference rows
that resolution reads are not snapshotted (only the resolved identity is pinned), and the session
sequence is the ``exchange_calendars`` release named by the methodology label (pinned by a test, not
recorded per manifest).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.benchmark import BenchmarkPriceBar
from quantforge.models.enums import (
    RESEARCH_PRICE_BASIS,
    AvailabilityBasis,
    EarningsTiming,
    PriceBasis,
    ReproducibilityGap,
    ReproducibilityLevel,
    SecFormType,
)
from quantforge.models.market import DailyPriceBar
from quantforge.observation.models import (
    AccountingState,
    DisclosureEvidenceLevel,
    FilingLinkStatus,
    ResearchObservation,
)
from quantforge.repositories.financial_fact import FinancialFactRepository
from quantforge.reproducibility.baseline import ManifestFingerprints, ReproducibilityBaseline
from quantforge.reproducibility.canonical import Fingerprint, canonical_text
from quantforge.reproducibility.methodology import CURRENT_METHODOLOGY, MethodologyIdentity
from quantforge.research.alignment import ALIGNMENT_WINDOW_DAYS
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturnStatus
from quantforge.research.event_returns import forward_price_window_end
from quantforge.research.returns import ForwardReturnStatus

__all__ = [
    "RESIDUAL_ASSUMPTIONS",
    "AccountingEvidence",
    "BarPin",
    "BenchmarkBarPin",
    "BenchmarkEvidence",
    "EventEvidence",
    "EvidenceChangedDuringAssemblyError",
    "EvidenceManifest",
    "EvidenceState",
    "FactPin",
    "FilingEvidence",
    "PitEvidence",
    "ReproducibilityGap",
    "ReproducibilityLevel",
    "ReproducibilityManifest",
    "ReproducibilityRecord",
    "RequestIdentity",
    "SessionEvidence",
    "StockEvidence",
    "build_reproducibility_record",
]


class EvidenceChangedDuringAssemblyError(RuntimeError):
    """A consumed bar disappeared or was re-sourced between the study and the manifest read.

    Not an ordinary result: the evidence moved under the computation (READ COMMITTED). Retry.
    """


RESIDUAL_ASSUMPTIONS: Final[tuple[str, ...]] = (
    "STORED_PRICE_BARS_AND_FILINGS_ARE_IMMUTABLE_BY_CONVENTION",
    "LISTING_REFERENCE_ROWS_ARE_NOT_SNAPSHOTTED_ONLY_THE_RESOLVED_IDENTITY_IS_PINNED",
    "SESSION_SEQUENCE_IS_THE_EXCHANGE_CALENDAR_RELEASE_PINNED_BY_THE_METHODOLOGY_LABEL_ONLY",
)


class EvidenceState(StrEnum):
    """Distinguishes the absent cases deterministically."""

    #: Not asked for (no benchmark / no filing).
    NOT_REQUESTED = "NOT_REQUESTED"
    #: Asked for, but an upstream refusal meant nothing was read (no resolved listing, link refused, ...).
    NOT_READ = "NOT_READ"
    #: Read; the pins below describe what was read (possibly nothing found).
    PINNED = "PINNED"


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    event_id: uuid.UUID
    horizons: tuple[int, ...]
    benchmark_id: uuid.UUID | None
    security_id: uuid.UUID | None
    stock_source_id: uuid.UUID | None
    benchmark_source_id: uuid.UUID | None
    price_basis: PriceBasis
    filing_accession: str | None
    eps_period_class: str | None
    eps_kind: str | None
    modes: tuple[ResearchMode, ...]


@dataclass(frozen=True, slots=True)
class EventEvidence:
    event_id: uuid.UUID
    fingerprint: str
    source_id: uuid.UUID
    ingestion_run_id: uuid.UUID | None
    event_date: date
    timing: EarningsTiming


@dataclass(frozen=True, slots=True)
class PitEvidence:
    """What the availability policy was given. No availability timestamp is ever manufactured."""

    eligibility: PointInTimeEligibility
    observed_at: datetime | None
    observed_at_basis: AvailabilityBasis | None


@dataclass(frozen=True, slots=True)
class SessionEvidence:
    """The stored stock-bar dates the study could see (alignment + forward-return windows)."""

    window_start: date
    window_end: date
    session_count: int
    sessions_sha256: str


@dataclass(frozen=True, slots=True)
class BarPin:
    session: date
    bar_id: uuid.UUID
    source_id: uuid.UUID
    close: Decimal
    ingested_at: datetime
    ingestion_run_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class StockEvidence:
    state: EvidenceState
    listing_id: uuid.UUID | None
    security_id: uuid.UUID | None
    price_basis: PriceBasis
    sessions: SessionEvidence | None
    price_source_id: uuid.UUID | None
    bars: tuple[BarPin, ...]


@dataclass(frozen=True, slots=True)
class BenchmarkBarPin:
    """A consumed benchmark level. ``ingestion_run_id`` is None for every pre-0013 row (unknown,
    never inferred) and for levels loaded outside a recorded run."""

    session: date
    bar_id: uuid.UUID
    source_id: uuid.UUID
    close: Decimal
    ingested_at: datetime
    ingestion_run_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class BenchmarkEvidence:
    state: EvidenceState
    benchmark_id: uuid.UUID | None
    price_basis: PriceBasis
    window_start: date | None
    window_end: date | None
    session_count: int | None
    sessions_sha256: str | None
    bars: tuple[BenchmarkBarPin, ...]


@dataclass(frozen=True, slots=True)
class FilingEvidence:
    state: EvidenceState
    accession_number: str | None
    form: SecFormType | None
    source_id: uuid.UUID | None
    ingestion_run_id: uuid.UUID | None
    #: Stored 8-K item codes, verbatim (D0/D1 derive from these; they are not a time).
    items: str | None
    disclosure_level: DisclosureEvidenceLevel


@dataclass(frozen=True, slots=True)
class FactPin:
    role: str
    fact_id: uuid.UUID
    tag: str
    taxonomy_version: str
    unit: str
    period_end: date
    qtrs: int
    value: Decimal | None
    segments: str
    coreg: str
    artifact_sha256: str


@dataclass(frozen=True, slots=True)
class AccountingEvidence:
    state: EvidenceState
    #: FSDS packages (name, sha256) that carry the filing's stored facts; pins the whole fact set,
    #: so a refusal that rests on a fact's absence is reproducible against the same packages.
    artifacts: tuple[tuple[str, str], ...]
    facts: tuple[FactPin, ...]


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    event: EventEvidence
    listing_resolution_status: str
    listing_evidence: str | None
    pit: PitEvidence
    stock: StockEvidence
    benchmark: BenchmarkEvidence
    filing: FilingEvidence
    accounting: AccountingEvidence


@dataclass(frozen=True, slots=True)
class ReproducibilityManifest:
    request: RequestIdentity
    methodology: MethodologyIdentity
    evidence: EvidenceManifest


@dataclass(frozen=True, slots=True)
class ReproducibilityRecord:
    """The typed manifest of a fresh computation plus the baseline derived from its canonical text.

    Fingerprints, gaps and level all come from the baseline (one derivation, shared with records
    loaded from the database); the typed ``manifest`` is for inspection by code that just built it.
    """

    manifest: ReproducibilityManifest
    baseline: ReproducibilityBaseline
    residual_assumptions: tuple[str, ...] = RESIDUAL_ASSUMPTIONS

    @property
    def fingerprints(self) -> ManifestFingerprints:
        return self.baseline.fingerprints

    #: Fingerprint of the computed observation (roadmap ``known_gaps`` excluded): "did the output change".
    @property
    def result_fingerprint(self) -> Fingerprint:
        return self.baseline.result_fingerprint

    @property
    def gaps(self) -> tuple[ReproducibilityGap, ...]:
        return self.baseline.gaps

    @property
    def level(self) -> ReproducibilityLevel:
        return self.baseline.level

    @property
    def canonical_manifest(self) -> str:
        return self.baseline.canonical_manifest

    @property
    def canonical_result(self) -> str:
        return self.baseline.canonical_result


def _sessions_digest(pairs: list[tuple[date, uuid.UUID]]) -> str:
    """SHA-256 over the sorted distinct ``date|source`` pairs: a second source appearing on an
    already-held date changes the digest (it can turn the study ambiguous)."""
    material = ",".join(f"{day.isoformat()}|{source}" for day, source in sorted(set(pairs)))
    return hashlib.sha256(material.encode("ascii")).hexdigest()


def _request(observation: ResearchObservation) -> RequestIdentity:
    p = observation.parameters
    return RequestIdentity(
        event_id=observation.identity.event_id,
        horizons=tuple(p.horizons),
        benchmark_id=p.benchmark_id,
        security_id=p.security_id,
        stock_source_id=p.stock_source_id,
        benchmark_source_id=p.benchmark_source_id,
        price_basis=p.price_basis,
        filing_accession=p.filing_accession,
        eps_period_class=None if p.eps_period_class is None else p.eps_period_class.value,
        eps_kind=None if p.eps_kind is None else p.eps_kind.value,
        modes=(ResearchMode.DESCRIPTIVE, ResearchMode.POINT_IN_TIME),
    )


def _stock_evidence(session: Session, observation: ResearchObservation) -> StockEvidence:
    study = observation.descriptive.study
    listing = observation.identity.listing_resolution
    if listing.listing_id is None:
        return StockEvidence(
            EvidenceState.NOT_READ, None, listing.security_id, RESEARCH_PRICE_BASIS, None, None, ()
        )
    returns = study.stock_returns
    event_date = observation.event.event_date
    window_start = event_date - timedelta(days=ALIGNMENT_WINDOW_DAYS)
    window_end = event_date + timedelta(days=ALIGNMENT_WINDOW_DAYS)
    if returns.anchor_session is not None:
        window_end = max(
            window_end,
            forward_price_window_end(returns.anchor_session, observation.parameters.horizons),
        )
    rows = session.execute(
        sa.select(
            DailyPriceBar.id,
            DailyPriceBar.trading_date,
            DailyPriceBar.source_id,
            DailyPriceBar.close,
            DailyPriceBar.ingested_at,
            DailyPriceBar.ingestion_run_id,
        )
        .where(
            DailyPriceBar.listing_id == listing.listing_id,
            DailyPriceBar.price_basis == RESEARCH_PRICE_BASIS,
            DailyPriceBar.trading_date >= window_start,
            DailyPriceBar.trading_date <= window_end,
        )
        .order_by(DailyPriceBar.trading_date, DailyPriceBar.source_id)
    ).all()
    dates = [row.trading_date for row in rows]
    pairs = [(row.trading_date, row.source_id) for row in rows]
    consumed: set[date] = set()
    for forward in returns.forward_returns:
        if forward.status is ForwardReturnStatus.AVAILABLE:
            if forward.anchor_session is None or forward.target_session is None:
                message = "an AVAILABLE forward return must carry its anchor and target sessions"
                raise RuntimeError(message)
            consumed |= {forward.anchor_session, forward.target_session}
    pins: list[BarPin] = []
    for row in rows:
        if row.trading_date in consumed and row.source_id == returns.price_source_id:
            pins.append(
                BarPin(
                    session=row.trading_date,
                    bar_id=row.id,
                    source_id=row.source_id,
                    close=row.close,
                    ingested_at=row.ingested_at,
                    ingestion_run_id=row.ingestion_run_id,
                )
            )
    if {pin.session for pin in pins} != consumed:
        message = "a consumed stock bar is no longer stored: the evidence changed during assembly"
        raise EvidenceChangedDuringAssemblyError(message)
    return StockEvidence(
        state=EvidenceState.PINNED,
        listing_id=listing.listing_id,
        security_id=listing.security_id,
        price_basis=RESEARCH_PRICE_BASIS,
        sessions=SessionEvidence(
            window_start, window_end, len(set(dates)), _sessions_digest(pairs)
        ),
        price_source_id=returns.price_source_id,
        bars=tuple(sorted(pins, key=lambda pin: pin.session)),
    )


def _benchmark_evidence(session: Session, observation: ResearchObservation) -> BenchmarkEvidence:
    study = observation.descriptive.study
    benchmark_id = observation.parameters.benchmark_id
    if benchmark_id is None:
        return BenchmarkEvidence(
            EvidenceState.NOT_REQUESTED, None, RESEARCH_PRICE_BASIS, None, None, None, None, ()
        )
    available = [
        r
        for r in study.stock_returns.forward_returns
        if r.status is ForwardReturnStatus.AVAILABLE
        and r.anchor_session is not None
        and r.target_session is not None
    ]
    if not available:  # the benchmark engine reads nothing without an available stock return
        return BenchmarkEvidence(
            EvidenceState.NOT_READ, benchmark_id, RESEARCH_PRICE_BASIS, None, None, None, None, ()
        )
    window_start = min(r.anchor_session for r in available if r.anchor_session is not None)
    window_end = max(r.target_session for r in available if r.target_session is not None)
    query = sa.select(
        BenchmarkPriceBar.id,
        BenchmarkPriceBar.trading_date,
        BenchmarkPriceBar.source_id,
        BenchmarkPriceBar.close,
        BenchmarkPriceBar.ingested_at,
        BenchmarkPriceBar.ingestion_run_id,
    ).where(
        BenchmarkPriceBar.benchmark_id == benchmark_id,
        BenchmarkPriceBar.price_basis == RESEARCH_PRICE_BASIS,
        BenchmarkPriceBar.trading_date >= window_start,
        BenchmarkPriceBar.trading_date <= window_end,
    )
    pinned_source = observation.parameters.benchmark_source_id
    if pinned_source is not None:
        query = query.where(BenchmarkPriceBar.source_id == pinned_source)
    rows = session.execute(
        query.order_by(BenchmarkPriceBar.trading_date, BenchmarkPriceBar.source_id)
    ).all()
    dates = [row.trading_date for row in rows]
    pairs = [(row.trading_date, row.source_id) for row in rows]
    consumed_returns = [
        b for b in study.benchmark_returns if b.status is BenchmarkReturnStatus.AVAILABLE
    ]
    consumed: set[date] = set()
    for item in consumed_returns:
        if item.anchor_session is None or item.target_session is None:
            message = "an AVAILABLE benchmark return must carry its anchor and target sessions"
            raise RuntimeError(message)
        consumed |= {item.anchor_session, item.target_session}
    candidates = [row for row in rows if row.trading_date in consumed]
    sources = {row.source_id for row in candidates}
    if len(sources) > 1:
        message = "consumed benchmark levels come from more than one source"
        raise EvidenceChangedDuringAssemblyError(message)
    pins = tuple(
        BenchmarkBarPin(
            row.trading_date,
            row.id,
            row.source_id,
            row.close,
            row.ingested_at,
            row.ingestion_run_id,
        )
        for row in candidates
    )
    if {pin.session for pin in pins} != consumed:
        message = "a consumed benchmark level is no longer stored: the evidence changed"
        raise EvidenceChangedDuringAssemblyError(message)
    return BenchmarkEvidence(
        state=EvidenceState.PINNED,
        benchmark_id=benchmark_id,
        price_basis=RESEARCH_PRICE_BASIS,
        window_start=window_start,
        window_end=window_end,
        session_count=len(set(dates)),
        sessions_sha256=_sessions_digest(pairs),
        bars=pins,
    )


def _filing_and_accounting(
    session: Session, observation: ResearchObservation
) -> tuple[FilingEvidence, AccountingEvidence]:
    link = observation.filing_link
    evidence = observation.disclosure_evidence
    if link.status is FilingLinkStatus.NOT_LINKED:
        return (
            FilingEvidence(
                EvidenceState.NOT_REQUESTED, None, None, None, None, None, evidence.level
            ),
            AccountingEvidence(EvidenceState.NOT_REQUESTED, (), ()),
        )
    if link.status is not FilingLinkStatus.CALLER_SUPPLIED_CIK_CONSISTENT:
        # requested but refused: only the request (in the manifest) and the refusal (in the result)
        return (
            FilingEvidence(
                EvidenceState.NOT_READ,
                link.accession_number,
                None,
                None,
                None,
                None,
                evidence.level,
            ),
            AccountingEvidence(EvidenceState.NOT_READ, (), ()),
        )
    provenance = observation.provenance
    filing = FilingEvidence(
        state=EvidenceState.PINNED,
        accession_number=link.accession_number,
        form=link.form,
        source_id=provenance.filing_source_id,
        ingestion_run_id=provenance.filing_ingestion_run_id,
        items=evidence.items,
        disclosure_level=evidence.level,
    )
    change = observation.accounting.change
    if observation.accounting.state is not AccountingState.EVALUATED or change is None:
        message = "a consistent filing link always evaluates accounting"
        raise RuntimeError(message)
    assert link.accession_number is not None
    artifacts = tuple(
        FinancialFactRepository(session).artifacts_for_filing(
            company_id=observation.identity.company_id, accession_number=link.accession_number
        )
    )
    digests = dict(provenance.accounting_artifact_sha256)
    pins: list[FactPin] = []
    current = change.comparative.current
    if current.fact_id is not None:
        if (
            current.tag is None
            or current.taxonomy_version is None
            or current.unit is None
            or current.period_end is None
            or current.duration_quarters is None
            or current.segments is None
            or current.coreg is None
        ):
            message = "the current observation carries a partial fact"
            raise RuntimeError(message)
        pins.append(
            FactPin(
                "current",
                current.fact_id,
                current.tag,
                current.taxonomy_version,
                current.unit,
                current.period_end,
                current.duration_quarters,
                current.value,
                current.segments,
                current.coreg,
                digests["current"],
            )
        )
    comparative = change.comparative.comparative_fact
    if comparative is not None:
        pins.append(
            FactPin(
                "comparative",
                comparative.fact_id,
                comparative.tag,
                comparative.taxonomy_version,
                comparative.unit,
                comparative.period_end,
                comparative.duration_quarters,
                comparative.value,
                comparative.segments,
                comparative.coreg,
                digests["comparative"],
            )
        )
    return filing, AccountingEvidence(EvidenceState.PINNED, artifacts, tuple(pins))


def _canonical_result(observation: ResearchObservation) -> str:
    """The observation's canonical text; the internal roadmap ``known_gaps`` list is not a result."""
    stripped = replace(observation, provenance=replace(observation.provenance, known_gaps=()))
    return canonical_text(stripped)


def build_reproducibility_record(
    session: Session,
    observation: ResearchObservation,
    *,
    methodology: MethodologyIdentity = CURRENT_METHODOLOGY,
) -> ReproducibilityRecord:
    """The manifest, fingerprints and evidence-derived level of an already assembled observation.

    Read-only: at most three SELECTs (stock bars in the read window, benchmark levels, the filing's
    FSDS packages), each only when that evidence was actually consumed. Identities already present
    in the observation (event, filing, fact, artifact digest) are reused, not re-queried.
    """
    event = observation.event
    provenance = observation.provenance
    filing, accounting = _filing_and_accounting(session, observation)
    evidence = EvidenceManifest(
        event=EventEvidence(
            event_id=event.event_id,
            fingerprint=event.fingerprint,
            source_id=provenance.event_source_id,
            ingestion_run_id=provenance.event_ingestion_run_id,
            event_date=event.event_date,
            timing=event.timing,
        ),
        listing_resolution_status=observation.identity.listing_resolution.status.value,
        listing_evidence=(
            None
            if observation.identity.listing_resolution.evidence is None
            else observation.identity.listing_resolution.evidence.value
        ),
        pit=PitEvidence(
            eligibility=observation.point_in_time.eligibility,
            observed_at=event.observed_at,
            observed_at_basis=event.observed_at_basis,
        ),
        stock=_stock_evidence(session, observation),
        benchmark=_benchmark_evidence(session, observation),
        filing=filing,
        accounting=accounting,
    )
    manifest = ReproducibilityManifest(_request(observation), methodology, evidence)
    baseline = ReproducibilityBaseline.from_canonical(
        canonical_text(manifest), _canonical_result(observation)
    )
    return ReproducibilityRecord(manifest=manifest, baseline=baseline)
