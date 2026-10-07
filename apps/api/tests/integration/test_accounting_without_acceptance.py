"""Descriptive accounting needs filing identity and FSDS evidence, never ``accepted_at`` (step 11.2H).

Permanent regression: with every stored filing's acceptance instant NULL (the state bulk SEC
identity ingestion now produces), FSDS binding, the EPS selectors, the descriptive change, the
filing EPS API, the filings index and the fact trace all work and are byte-identical to the
responses with an instant established.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10Q
from tests.integration.test_eps_extraction import (  # noqa: F401 - fixtures
    _extract,
    _ingest,
    _nums,
    company,
    filings,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import FinancialFact
from quantforge.repositories.financial_fact import FinancialFactRepository

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("filings")]


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


def _responses(client: TestClient) -> dict[str, Any]:
    eps = client.get(
        f"/api/v1/filings/{ACC_10Q}/eps", params={"period_class": "QUARTER", "eps_kind": "DILUTED"}
    )
    index = client.get("/api/v1/filings")
    assert eps.status_code == index.status_code == 200
    return {"eps": eps.json(), "index": index.json()}


def test_every_accounting_path_is_identical_with_the_acceptance_instant_null(
    db_session: Session,
    tmp_path: Path,
    client: TestClient,
) -> None:
    _ingest(db_session, tmp_path, _nums())
    with_instant = _responses(client)
    observation = _extract(db_session, ACC_10Q)

    db_session.execute(sa.update(SecFiling).values(accepted_at=None))
    db_session.commit()

    assert _responses(client) == with_instant, "the API cannot depend on accepted_at"
    again = _extract(db_session, ACC_10Q)
    assert again.status is observation.status and again.value == observation.value
    fact_id = db_session.scalars(sa.select(FinancialFact.id)).first()
    assert fact_id is not None
    trace = FinancialFactRepository(db_session).trace(fact_id)
    assert trace is not None and trace.filing_accepted_at is None


def test_fsds_binding_works_against_filings_that_have_no_acceptance_instant(
    db_session: Session, tmp_path: Path
) -> None:
    db_session.execute(sa.update(SecFiling).values(accepted_at=None))
    db_session.commit()

    _ingest(db_session, tmp_path, _nums())

    assert db_session.query(FinancialFact).count() > 0
    assert _extract(db_session, ACC_10Q).value is not None


def test_a_filing_row_can_hold_a_null_and_the_established_value_is_a_utc_instant(
    db_session: Session,
) -> None:
    row = db_session.scalars(
        sa.select(SecFiling).where(SecFiling.accession_number == ACC_10Q)
    ).one()
    row.accepted_at = None
    db_session.commit()
    db_session.refresh(row)
    assert row.accepted_at is None

    row.accepted_at = datetime(2026, 4, 1, 20, 16, 39, tzinfo=UTC)
    db_session.commit()
    db_session.refresh(row)
    assert row.accepted_at == datetime(2026, 4, 1, 20, 16, 39, tzinfo=UTC)
