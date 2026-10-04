"""One coherent, test-only SEC FSDS scenario for the descriptive accounting vertical.

Companies A and B (CIKs) and C (no CIK); filings covering the happy path, a joint accession (same
accession under A and B with deliberately different EPS), a comparative period mismatch, a profit to
loss annual, zero and negative comparatives, a null comparative, a missing current EPS, a null
current value, a CIK-less registrant and a tiny ratio. Rows are inserted directly: nothing is
ingested, fetched or seeded into production code.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import FinancialFactSourceKind, ProviderType, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import (
    FinancialFact,
    FinancialFactArtifact,
    FinancialFactSubmission,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
CIK_A = "0000320193"
CIK_B = "0000555555"
EPS_TAG = "EarningsPerShareDiluted"
TAXONOMY = "us-gaap/2024"

ACC_HAPPY = "0000320193-24-000010"  # A: 1.5300 vs 1.5200; also stored under B (joint)
ACC_MISMATCH = "0000320193-24-000020"  # 10-Q/A: the earlier fact is not 12 month-ends before
ACC_ANNUAL = "0000320193-23-000106"  # 10-K: profit to loss
ACC_ZERO = "0000320193-24-000030"
ACC_NEGATIVE = "0000320193-24-000040"
ACC_NULL_COMPARATIVE = "0000320193-24-000050"
ACC_MISSING_CURRENT = "0000320193-24-000060"
ACC_NO_CIK = "0000320193-24-000070"
ACC_TINY = "0000320193-24-000080"
ACC_NULL_CURRENT = "0000320193-24-000090"
ACC_UNKNOWN = "0000111111-24-000001"


@dataclass(frozen=True)
class Spec:
    accession: str
    company: str  # "A", "B" or "C" (no CIK)
    form: SecFormType
    filing_date: date
    period_end: date
    qtrs: int
    facts: tuple[tuple[date, str | None], ...]  # (period_end, value) at this qtrs


Q1_2024 = date(2024, 3, 31)
Q1_2023 = date(2023, 3, 31)

SPECS: tuple[Spec, ...] = (
    Spec(
        ACC_HAPPY,
        "A",
        SecFormType.FORM_10Q,
        date(2024, 5, 3),
        Q1_2024,
        1,
        ((Q1_2024, "1.5300"), (Q1_2023, "1.5200")),
    ),
    Spec(
        ACC_HAPPY,
        "B",
        SecFormType.FORM_10Q,
        date(2024, 5, 3),
        Q1_2024,
        1,
        ((Q1_2024, "4.0000"), (Q1_2023, "3.0000")),
    ),
    Spec(
        ACC_MISMATCH,
        "A",
        SecFormType.FORM_10Q_A,
        date(2024, 5, 17),
        Q1_2024,
        1,
        ((Q1_2024, "1.5300"), (date(2023, 2, 28), "1.5200")),
    ),
    Spec(
        ACC_ANNUAL,
        "A",
        SecFormType.FORM_10K,
        date(2023, 11, 3),
        date(2023, 9, 30),
        4,
        ((date(2023, 9, 30), "-0.5000"), (date(2022, 9, 30), "1.0000")),
    ),
    Spec(
        ACC_ZERO,
        "A",
        SecFormType.FORM_10Q,
        date(2024, 6, 1),
        Q1_2024,
        1,
        ((Q1_2024, "0.5000"), (Q1_2023, "0.0000")),
    ),
    Spec(
        ACC_NEGATIVE,
        "A",
        SecFormType.FORM_10Q,
        date(2024, 6, 2),
        Q1_2024,
        1,
        ((Q1_2024, "-0.5000"), (Q1_2023, "-1.0000")),
    ),
    Spec(
        ACC_NULL_COMPARATIVE,
        "A",
        SecFormType.FORM_10Q,
        date(2024, 6, 3),
        Q1_2024,
        1,
        ((Q1_2024, "1.5300"), (Q1_2023, None)),
    ),
    Spec(ACC_MISSING_CURRENT, "A", SecFormType.FORM_10Q, date(2024, 6, 4), Q1_2024, 1, ()),
    Spec(
        ACC_NO_CIK,
        "C",
        SecFormType.FORM_10Q,
        date(2024, 6, 5),
        Q1_2024,
        1,
        ((Q1_2024, "2.0000"), (Q1_2023, "1.0000")),
    ),
    Spec(
        ACC_TINY,
        "A",
        SecFormType.FORM_10Q,
        date(2024, 6, 6),
        Q1_2024,
        1,
        ((Q1_2024, "100.0001"), (Q1_2023, "100.0000")),
    ),
    Spec(
        ACC_NULL_CURRENT,
        "A",
        SecFormType.FORM_10Q,
        date(2024, 6, 7),
        Q1_2024,
        1,
        ((Q1_2024, None), (Q1_2023, "1.0000")),
    ),
)


def seed_scenario(session: Session) -> None:
    """Insert the scenario. Every request in a test then reads only stored rows."""
    source = get_or_create_data_source(
        session, name="sec-edgar", provider_type=ProviderType.REGULATORY_FILING
    )
    companies = {
        key: Company(legal_name=f"Co {key}", display_name=f"Co {key}", cik=cik)
        for key, cik in (("A", CIK_A), ("B", CIK_B), ("C", None))
    }
    session.add_all(companies.values())
    artifact = FinancialFactArtifact(
        source_id=source.id,
        source_kind=FinancialFactSourceKind.SEC_FSDS,
        artifact_name="vertical",
        sha256=hashlib.sha256(b"vertical").hexdigest(),
        byte_length=10,
        raw_artifact_path="raw/vertical.zip",
        member_digests={},
        num_has_segments=True,
        ingested_at=NOW,
        created_at=NOW,
    )
    session.add(artifact)
    session.flush()
    for spec in SPECS:
        filing = SecFiling(
            company_id=companies[spec.company].id,
            accession_number=spec.accession,
            form=spec.form,
            filing_date=spec.filing_date,
            accepted_at=datetime(2024, 5, 3, 20, 30, tzinfo=UTC),
            source_id=source.id,
            ingested_at=NOW,
            created_at=NOW,
        )
        session.add(filing)
        session.flush()
        submission = FinancialFactSubmission(
            artifact_id=artifact.id,
            sec_filing_id=filing.id,
            period_end=spec.period_end,
            coregistrant_count=1,
            created_at=NOW,
        )
        session.add(submission)
        session.flush()
        for period_end, value in spec.facts:
            session.add(
                FinancialFact(
                    sec_filing_id=filing.id,
                    submission_id=submission.id,
                    tag=EPS_TAG,
                    version=TAXONOMY,
                    period_end=period_end,
                    duration_quarters=spec.qtrs,
                    unit="USD",
                    segments="",
                    coreg="",
                    value=None if value is None else Decimal(value),
                    created_at=NOW,
                )
            )
    session.commit()
