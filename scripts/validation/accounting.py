"""11.2F accounting golden validation. Usage (repo root, so .env is found):

    uv run --project apps/api python scripts/validation/accounting.py <stage>

Stages: sec-filing (<=2 SEC requests), fsds-ingest (offline), selectors (offline, DB), api (in-process).
Orchestration only: every business rule is the production code path. Never prints a credential.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime

import common

CIK = "0000320187"
ACCESSION = "0000320187-26-000037"
PACKAGE = common.ART / "fsds" / "2026q2.zip"
PACKAGE_SHA = "d7c815395cd420cfbe09b29dbae73a45406c5d0652d5ae1859ae11ea2e22df77"
PACKAGE_BYTES = 60_419_016


def _session():  # noqa: ANN202
    from quantforge.db.session import get_session_factory

    settings = common.validation_settings()
    return get_session_factory(settings)(), settings


def _sql(session, text, **params):  # noqa: ANN001, ANN202
    import sqlalchemy as sa

    return [tuple(r) for r in session.execute(sa.text(text), params)]


def verify_package() -> None:
    data = PACKAGE.read_bytes()
    assert len(data) == PACKAGE_BYTES, f"size mismatch {len(data)}"
    assert hashlib.sha256(data).hexdigest() == PACKAGE_SHA, "SHA mismatch: STOP, do not use the package"
    print("FSDS package verified: bytes", len(data), "sha256", PACKAGE_SHA)


async def stage_sec_filing() -> None:
    from quantforge.core.config import get_settings
    from quantforge.ingestion.cli import _commit_failed_run
    from quantforge.ingestion.filing_history import FilingHistoryIngestionService
    from quantforge.providers.sec.client import SecClient, SecConfig

    base = get_settings()
    config = SecConfig(
        user_agent=base.sec_user_agent,
        base_url=base.sec_base_url,
        request_timeout=base.sec_request_timeout_seconds,
        max_retries=base.sec_max_retries,
        min_request_interval=base.sec_min_request_interval_seconds,
    )
    config.require_user_agent()
    session, _ = _session()
    t0 = time.monotonic()
    async with SecClient(config, transport=common.CountingTransport("sec")) as client:
        service = FilingHistoryIngestionService(
            session=session, provider=client, store=common.raw_store(), now=datetime.now(UTC)
        )
        try:
            report = await service.ingest_filing_history(
                CIK, accessions=[ACCESSION], max_history_files=1
            )
        except BaseException:
            _commit_failed_run(session)
            raise
        session.commit()
    elapsed = time.monotonic() - t0
    r, o = report.result, report.outcome
    print(f"outcome received={o.records_received} written={o.records_written} skipped={o.records_skipped} failed={o.records_failed}")
    print("recent rows seen", r.recent_rows_seen, "recent parsed", r.recent_filings_parsed)
    print("history files discovered", r.history_files_discovered, "fetched", r.history_files_fetched, "skipped", r.history_files_skipped)
    print("inserted", report.inserted, "matched_existing", report.matched_existing)
    print("requested", len(r.requested_accessions), "unresolved", list(r.unresolved_accessions), "located_not_stored", list(r.located_not_stored))
    print(f"duration {elapsed:.3f}s")
    print("ledger:", {k: v for k, v in common.ledger().items() if k != "requests"})
    rows = _sql(session, "select accession_number, form, filing_date, report_date, accepted_at, primary_document, ingestion_run_id is not null from sec_filing order by filing_date desc")
    print("sec_filing rows:", len(rows))
    for row in rows:
        mark = "  <== TARGET" if row[0] == ACCESSION else ""
        print(" ", row, mark)


async def stage_enrich() -> None:
    """ONE header request for the target accession, through the production service."""
    from quantforge.core.config import get_settings
    from quantforge.ingestion.cli import _commit_failed_run
    from quantforge.ingestion.filing_acceptance import FilingAcceptanceService
    from quantforge.providers.sec.client import SecClient, SecConfig

    base = get_settings()
    config = SecConfig(
        user_agent=base.sec_user_agent,
        base_url=base.sec_base_url,
        request_timeout=base.sec_request_timeout_seconds,
        max_retries=base.sec_max_retries,
        min_request_interval=base.sec_min_request_interval_seconds,
    )
    session, _ = _session()
    t0 = time.monotonic()
    async with SecClient(config, transport=common.CountingTransport("sec")) as client:
        service = FilingAcceptanceService(
            session=session, provider=client, store=common.raw_store(), now=datetime.now(UTC)
        )
        try:
            report = await service.enrich(ACCESSION, cik=CIK)
        except BaseException:
            _commit_failed_run(session)
            raise
        session.commit()
    print(f"status {report.status.value} accepted_at {report.accepted_at.isoformat()} source {report.source}")
    print(f"duration {time.monotonic() - t0:.3f}s", "| ledger:", {k: v for k, v in common.ledger().items() if k != "requests"})
    print("run:", _sql(session, "select dataset, status, records_received, records_written, request_parameters::text, raw_artifact_path is not null from ingestion_run order by started_at desc limit 1"))
    print("accepted_at in DB:", _sql(session, "select accepted_at from sec_filing where accession_number=:a", a=ACCESSION))
    print("other filings still NULL:", _sql(session, "select count(*) from sec_filing where accepted_at is null"))


async def stage_enrich_again() -> None:
    """A second call must make NO request (default mode)."""
    from quantforge.ingestion.filing_acceptance import FilingAcceptanceService

    class _Forbidden:
        name = "sec-edgar"

        async def get_filing_header(self, cik, accession):  # noqa: ANN001, ANN202
            raise AssertionError("a request was attempted")

    session, _ = _session()
    report = await FilingAcceptanceService(
        session=session, provider=_Forbidden(), store=common.raw_store(), now=datetime.now(UTC)
    ).enrich(ACCESSION, cik=CIK)
    print("second call:", report.status.value, report.accepted_at.isoformat(), "source", report.source, "| requests made: 0")


async def stage_replay() -> None:
    """Offline: re-run the bulk identity path from the retained raw submissions bytes."""
    import glob

    from quantforge.ingestion.filings import FilingIngestionService
    from quantforge.providers.base import RawCapture
    from quantforge.providers.filings import FilingsResult
    from quantforge.providers.sec.mapper import map_recent_filings

    # The earlier (11.2F) refused response was kept as rejected evidence; replay it: the same
    # bytes now map to identity, offline.
    path = [p for p in glob.glob(str(common.RAW_ROOT / "sec-edgar" / "*" / "*" / "*.json")) if not p.endswith(".meta.json") and "submissions-rejected" in p][0]
    body = open(path, "rb").read()
    document = json.loads(body)
    filings, rejected = map_recent_filings(document, cik=CIK)

    class _Replay:
        name = "sec-edgar"

        async def get_filings(self, cik: str) -> FilingsResult:
            capture = RawCapture(provider="sec-edgar", dataset="submissions", endpoint="/submissions/CIK{cik}.json", request_parameters={"cik": cik}, body=body, retrieved_at=datetime.now(UTC))
            return FilingsResult(filings=filings, rejected=tuple(rejected), captures=(capture,))

    session, _ = _session()
    outcome = await FilingIngestionService(session=session, provider=_Replay(), store=common.raw_store(), now=datetime.now(UTC)).ingest_filings(CIK)
    session.commit()
    print(f"replay outcome received={outcome.records_received} written={outcome.records_written} skipped={outcome.records_skipped} failed={outcome.records_failed}")
    print("accepted_at after replay:", _sql(session, "select accepted_at from sec_filing where accession_number=:a", a=ACCESSION))
    print("sec_filing rows:", _sql(session, "select count(*), count(accepted_at) from sec_filing"))


def stage_fsds_ingest() -> None:
    from quantforge.ingestion.financial_facts import FinancialFactIngestionService

    verify_package()
    session, _ = _session()
    t0 = time.monotonic()
    service = FinancialFactIngestionService(session=session, store=common.raw_store(), now=datetime.now(UTC))
    result = service.ingest_fsds_package(PACKAGE, package_name="2026q2", accessions=[ACCESSION], ciks=[])
    session.commit()
    elapsed = time.monotonic() - t0
    o = result.outcome
    print(f"outcome received={o.records_received} written={o.records_written} skipped={o.records_skipped} failed={o.records_failed}")
    print("bound", list(result.bound_accessions), "unbound", list(result.unbound_accessions), "out_of_scope", list(result.out_of_scope_forms))
    print(f"duration {elapsed:.3f}s")


def stage_provenance() -> None:
    session, _ = _session()
    print("runs:")
    for row in _sql(session, "select dataset, status, records_received, records_written, records_skipped, records_failed, raw_artifact_path is not null, raw_content_sha256 is not null from ingestion_run order by started_at"):
        print(" ", row)
    print("financial_fact_submission:")
    for row in _sql(session, "select s.accession_number, fs.period_end, fs.fiscal_year, fs.fiscal_period, fs.source_accepted, fs.coregistrant_count from financial_fact_submission fs join sec_filing s on s.id=fs.sec_filing_id"):
        print(" ", row)
    print("artifact:")
    for row in _sql(session, "select artifact_name, source_kind, byte_length, raw_artifact_path is not null, member_digests::text, ingestion_run_id is not null from financial_fact_artifact"):
        print(" ", row[:4], "members:", row[4][:140], "run:", row[5])
    print("filing accepted_at for target:", _sql(session, "select accepted_at from sec_filing where accession_number=:a", a=ACCESSION))
    print("fact count:", _sql(session, "select count(*) from financial_fact"))
    print("fact columns:", [r[0] for r in _sql(session, "select column_name from information_schema.columns where table_name='financial_fact' order by ordinal_position")])
    print("EPS facts for accession:")
    for row in _sql(
        session,
        "select f.tag, f.version, f.unit, f.period_end, f.duration_quarters, f.segments, f.coreg, f.value, f.ingestion_run_id is not null "
        "from financial_fact f join sec_filing s on s.id=f.sec_filing_id where s.accession_number=:a and f.tag like 'EarningsPerShare%' order by f.tag, f.period_end",
        a=ACCESSION,
    ):
        print(" ", row)


def stage_selectors() -> None:
    from quantforge.financials.comparative_eps_extraction import extract_comparative_eps_observation
    from quantforge.financials.eps import EpsKind, EpsPeriodClass
    from quantforge.financials.eps_change import compute_eps_change

    session, _ = _session()
    t0 = time.monotonic()
    obs = extract_comparative_eps_observation(
        session, ACCESSION, period_class=EpsPeriodClass.QUARTER, eps_kind=EpsKind.DILUTED, company_cik=CIK
    )
    t1 = time.monotonic()
    chg = compute_eps_change(obs)
    print(f"extract {t1 - t0:.4f}s")
    print("comparative status:", obs.status, "|", obs.reason)
    print("current:", obs.current.status, obs.current.value)
    cf = obs.comparative_fact
    print("comparative fact:", cf.tag, cf.taxonomy_version, cf.unit, cf.period_end, cf.duration_quarters, repr(cf.segments), repr(cf.coreg), cf.value)
    print("change:", chg.status, "change", getattr(chg, "eps_change", None), "ratio", getattr(chg, "percentage_change", None), "transition", getattr(chg, "transition", None))


def stage_api() -> None:
    """In-process ASGI calls against the validation DB (no network, no browser)."""
    from fastapi.testclient import TestClient

    from quantforge.core.config import get_settings
    from quantforge.main import create_app

    settings = common.validation_settings()
    get_settings.cache_clear()
    import os

    os.environ["QF_DATABASE_URL"] = settings.database_url
    get_settings.cache_clear()
    client = TestClient(create_app())
    for label, path in (
        ("eps", f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&eps_kind=DILUTED&cik={CIK}"),
        ("eps-no-cik", f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&eps_kind=DILUTED"),
        ("eps-wrong-cik", f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&eps_kind=DILUTED&cik=0000320193"),
        ("index", "/api/v1/filings"),
    ):
        t0 = time.monotonic()
        r = client.get(path)
        print(f"--- {label} HTTP {r.status_code} {time.monotonic() - t0:.4f}s")
        print(json.dumps(r.json(), indent=1))


def main() -> None:
    stage = sys.argv[1]
    if stage == "verify-package":
        verify_package()
    elif stage == "sec-filing":
        asyncio.run(stage_sec_filing())
    elif stage == "enrich":
        asyncio.run(stage_enrich())
    elif stage == "enrich-again":
        asyncio.run(stage_enrich_again())
    elif stage == "replay":
        asyncio.run(stage_replay())
    elif stage == "fsds-ingest":
        stage_fsds_ingest()
    elif stage == "provenance":
        stage_provenance()
    elif stage == "selectors":
        stage_selectors()
    elif stage == "api":
        stage_api()
    else:
        sys.exit(f"unknown stage {stage}")


if __name__ == "__main__":
    main()
