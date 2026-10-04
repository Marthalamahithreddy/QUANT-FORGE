"""Pure mapping from a :class:`ResearchObservation` to the public response schema.

No query, no mutation and no financial calculation: every value, status, reason and refusal is
copied from the domain objects, and the accounting section reuses the existing filing EPS presenter.
The domain's ``known_gaps`` roadmap list and anything not named in the schema are not exposed.
"""

from __future__ import annotations

from quantforge.api.v1.filing_eps_presenter import present_filing_eps
from quantforge.observation.models import ResearchObservation
from quantforge.research.event_study import EventStudyResult
from quantforge.schemas.research_observation import (
    AbnormalReturnOut,
    AccountingOut,
    AlignmentOut,
    ArtifactDigestOut,
    BenchmarkOut,
    BenchmarkReturnOut,
    DescriptiveOut,
    DisclosureEvidenceOut,
    EpsSurpriseOut,
    EventOut,
    FilingLinkOut,
    ForwardReturnOut,
    IdentityOut,
    ListingResolutionOut,
    ObservationParametersOut,
    PointInTimeOut,
    ProvenanceOut,
    RefusalOut,
    ResearchObservationResponse,
    SessionAlignmentOut,
    StockReturnsOut,
    StudyOut,
)

__all__ = ["present_research_observation"]


def _study(study: EventStudyResult) -> StudyOut:
    surprise = study.eps_surprise
    session_alignment = study.alignment.session_alignment
    return StudyOut(
        research_mode=study.research_mode,
        point_in_time_eligibility=study.point_in_time_eligibility,
        eps_surprise=EpsSurpriseOut(
            status=surprise.status,
            reported_eps=surprise.reported_eps,
            estimated_eps=surprise.estimated_eps,
            absolute_surprise=surprise.absolute_surprise,
            surprise_ratio=surprise.surprise_ratio,
            reason=surprise.reason,
        ),
        alignment=AlignmentOut(
            event_date=study.alignment.event_date,
            timing=study.alignment.timing,
            session_alignment=(
                None
                if session_alignment is None
                else SessionAlignmentOut(
                    status=session_alignment.status,
                    event_session=session_alignment.event_session,
                    first_post_event_session=session_alignment.first_post_event_session,
                    reason=session_alignment.reason,
                )
            ),
        ),
        stock_returns=StockReturnsOut(
            listing_id=study.stock_returns.listing_id,
            price_source_id=study.stock_returns.price_source_id,
            anchor_session=study.stock_returns.anchor_session,
            forward_returns=[
                ForwardReturnOut(
                    horizon=item.horizon,
                    status=item.status,
                    anchor_session=item.anchor_session,
                    anchor_close=item.anchor_close,
                    target_session=item.target_session,
                    target_close=item.target_close,
                    return_value=item.return_value,
                    reason=item.reason,
                )
                for item in study.stock_returns.forward_returns
            ],
        ),
        benchmark=BenchmarkOut(
            benchmark_id=study.benchmark_id,
            returns=[
                BenchmarkReturnOut(
                    horizon=item.horizon,
                    status=item.status,
                    anchor_session=item.anchor_session,
                    anchor_close=item.anchor_close,
                    target_session=item.target_session,
                    target_close=item.target_close,
                    benchmark_return=item.benchmark_return,
                    reason=item.reason,
                )
                for item in study.benchmark_returns
            ],
        ),
        abnormal_returns=[
            AbnormalReturnOut(
                horizon=item.horizon,
                status=item.status,
                abnormal_return=item.abnormal_return,
                reason=item.reason,
            )
            for item in study.abnormal_returns
        ],
        price_basis_note=study.price_basis_note,
    )


def present_research_observation(observation: ResearchObservation) -> ResearchObservationResponse:
    identity = observation.identity
    listing = identity.listing_resolution
    parameters = observation.parameters
    event = observation.event
    link = observation.filing_link
    evidence = observation.disclosure_evidence
    change = observation.accounting.change
    pit = observation.point_in_time
    provenance = observation.provenance
    return ResearchObservationResponse(
        identity=IdentityOut(
            company_id=identity.company_id,
            company_cik=identity.company_cik,
            event_id=identity.event_id,
            listing_resolution=ListingResolutionOut(
                status=listing.status,
                listing_id=listing.listing_id,
                security_id=listing.security_id,
                exchange_mic=listing.exchange_mic,
                evidence=listing.evidence,
                reason=listing.reason,
            ),
            benchmark_id=identity.benchmark_id,
            filing_accession=identity.filing_accession,
        ),
        parameters=ObservationParametersOut(
            horizons=list(parameters.horizons),
            price_basis=parameters.price_basis,
            security_id=parameters.security_id,
            benchmark_id=parameters.benchmark_id,
            filing_accession=parameters.filing_accession,
            eps_period_class=parameters.eps_period_class,
            eps_kind=parameters.eps_kind,
        ),
        event=EventOut(
            event_id=event.event_id,
            fingerprint=event.fingerprint,
            fiscal_year=event.fiscal_year,
            fiscal_quarter=event.fiscal_quarter,
            event_date=event.event_date,
            event_timestamp=event.event_timestamp,
            timing=event.timing,
            reported_eps=event.reported_eps,
            estimated_eps=event.estimated_eps,
            observed_at=event.observed_at,
            observed_at_basis=event.observed_at_basis,
        ),
        filing_link=FilingLinkOut(
            status=link.status,
            period_verification=link.period_verification,
            accession_number=link.accession_number,
            company_cik=link.company_cik,
            form=link.form,
            reason=link.reason,
        ),
        disclosure_evidence=DisclosureEvidenceOut(
            level=evidence.level,
            accession_number=evidence.accession_number,
            form=evidence.form,
            items=evidence.items,
        ),
        accounting=AccountingOut(
            state=observation.accounting.state,
            eps=None if change is None else present_filing_eps(change.comparative, change),
        ),
        descriptive=DescriptiveOut(
            state=observation.descriptive.state, study=_study(observation.descriptive.study)
        ),
        point_in_time=PointInTimeOut(
            state=pit.state,
            eligibility=pit.eligibility,
            study=None if pit.study is None else _study(pit.study),
        ),
        provenance=ProvenanceOut(
            event_fingerprint=provenance.event_fingerprint,
            event_source_id=provenance.event_source_id,
            event_ingestion_run_id=provenance.event_ingestion_run_id,
            stock_price_source_id=provenance.stock_price_source_id,
            benchmark_id=provenance.benchmark_id,
            filing_source_id=provenance.filing_source_id,
            filing_ingestion_run_id=provenance.filing_ingestion_run_id,
            accounting_artifacts=[
                ArtifactDigestOut(role=role, sha256=sha)
                for role, sha in provenance.accounting_artifact_sha256
            ],
        ),
        refusals=[
            RefusalOut(scope=item.scope, code=item.code, reason=item.reason, horizon=item.horizon)
            for item in observation.refusals
        ],
    )
