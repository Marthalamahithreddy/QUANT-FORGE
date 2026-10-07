"""11.2B capability probe. Usage (from the repo root so .env is found):

    uv run --project apps/api python scripts/validation/probe.py <stage>
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date

import common
from quantforge.core.config import get_settings
from quantforge.providers.massive.client import MassiveClient, MassiveConfig


def massive_client(settings, transport):  # noqa: ANN001
    config = MassiveConfig(
        api_key=settings.massive_api_key,
        base_url=settings.massive_base_url,
        request_timeout=settings.massive_request_timeout_seconds,
        max_retries=settings.massive_max_retries,
        max_pages=settings.massive_max_pages,
        retry_base_delay=settings.massive_retry_base_delay_seconds,
        min_request_interval=settings.massive_min_request_interval_seconds,
    )
    config.require_api_key()
    return MassiveClient(config, transport=transport)


def stage_env() -> None:
    s = get_settings()
    print("massive key:", common.present(s.massive_api_key))
    print("finnhub key:", common.present(s.finnhub_api_key))
    print("sec user-agent:", common.present(s.sec_user_agent))
    print("ledger:", {k: v for k, v in common.ledger().items() if k != "requests"})


async def stage_listing_probe() -> None:
    """ONE Massive request: dated snapshot for NKE on 2026-10-01. Stores raw, ingests nothing."""
    s = get_settings()
    store = common.raw_store()
    client = massive_client(s, common.CountingTransport("massive"))
    try:
        try:
            result = await client.get_ticker_snapshots("NKE", date(2026, 10, 1))
        except Exception as exc:  # noqa: BLE001
            print("RESULT: provider error:", type(exc).__name__, str(exc)[:300])
            caps = getattr(exc, "captures", ())
            for c in caps:
                print("captured status:", getattr(c, "http_status", None))
            return
    finally:
        await client.aclose()
    print("snapshots:", len(result.snapshots), "unsupported:", result.unsupported, "rejected:", result.rejected)
    stored = store.store_all(result.captures)
    for st in stored:
        print("stored raw:", st.artifact.relative_path if hasattr(st, "artifact") else st)
    for cap in result.captures:
        doc = json.loads(cap.body)
        print("endpoint:", cap.endpoint, "params:", dict(cap.request_parameters))
        print("top-level keys:", sorted(doc.keys()))
        for k in ("status", "count", "request_id"):
            if k in doc:
                print(f"  {k}:", doc[k])
        for r in doc.get("results", []):
            print("  record fields:", sorted(r.keys()))
            print("  record:", json.dumps({k: v for k, v in r.items()}, default=str))
    for snap in result.snapshots:
        print("canonical (mapper output):", snap)


def finnhub_client(settings, transport):  # noqa: ANN001
    from quantforge.providers.finnhub.client import FinnhubClient, FinnhubConfig

    return FinnhubClient(
        FinnhubConfig(api_key=settings.finnhub_api_key, base_url=settings.finnhub_base_url),
        transport=transport,
    )


async def stage_finnhub_depth() -> None:
    """ONE Finnhub request: an OLDER window for a stored ticker (history depth + shape). No ingestion."""
    s = get_settings()
    client = finnhub_client(s, common.CountingTransport("finnhub"))
    try:
        try:
            result = await client.get_earnings("AAPL", date(2026, 7, 1), date(2026, 8, 15))
        except Exception as exc:  # noqa: BLE001
            print("RESULT: provider error:", type(exc).__name__, str(exc)[:300])
            return
    finally:
        await client.aclose()
    common.raw_store().store_all(result.captures)
    print("events returned:", len(result.events))
    for ev in result.events:
        print(" ", ev)
    for cap in result.captures:
        doc = json.loads(cap.body)
        print("raw top-level keys:", sorted(doc.keys()) if isinstance(doc, dict) else type(doc))
        for row in (doc.get("earningsCalendar", []) if isinstance(doc, dict) else []):
            print("raw entry keys:", sorted(row.keys()))


def _session():  # noqa: ANN202
    from quantforge.db.session import get_session_factory

    settings = common.validation_settings()
    return get_session_factory(settings)(), settings


def _sql(session, text):  # noqa: ANN001, ANN202
    import sqlalchemy as sa

    return [tuple(r) for r in session.execute(sa.text(text))]


async def stage_reference() -> None:
    from datetime import UTC, datetime

    from quantforge.ingestion.runs import FAILED_RUN_RECORDED
    from quantforge.ingestion.service import IngestionService

    session, settings = _session()
    client = massive_client(settings, common.CountingTransport("massive"))
    try:
        svc = IngestionService(session=session, provider=client, store=common.raw_store(), now=datetime.now(UTC))
        try:
            outcome, rows = await svc.ingest_reference(["NKE"])
            session.commit()
        except Exception as exc:  # noqa: BLE001
            print("ERROR", type(exc).__name__, str(exc)[:300])
            if session.info.pop(FAILED_RUN_RECORDED, False):
                session.commit()
            else:
                session.rollback()
            return
        print("outcome:", outcome)
    finally:
        await client.aclose()
    print(_sql(session, "select status, dataset, records_written from ingestion_run order by started_at"))
    print(_sql(session, "select c.legal_name, c.cik, l.ticker, l.valid_from from company c join security s on s.company_id=c.id join listing l on l.security_id=s.id"))


async def stage_bars() -> None:
    from datetime import UTC, datetime

    from quantforge.ingestion.runs import FAILED_RUN_RECORDED
    from quantforge.ingestion.service import IngestionService
    from quantforge.models.enums import PriceBasis

    start, end = date(2026, 9, 1), date(2026, 10, 6)
    session, settings = _session()
    for label in ("stock", "benchmark"):
        client = massive_client(settings, common.CountingTransport("massive"))
        try:
            svc = IngestionService(session=session, provider=client, store=common.raw_store(), now=datetime.now(UTC))
            try:
                if label == "stock":
                    outcome = await svc.ingest_daily_bars("NKE", start, end, price_basis=PriceBasis.SPLIT_ADJUSTED)
                else:
                    outcome = await svc.ingest_benchmark_daily_bars(
                        "SPY", start, end, name="SPY (ETF proxy for the S&P 500)", price_basis=PriceBasis.SPLIT_ADJUSTED
                    )
                session.commit()
            except Exception as exc:  # noqa: BLE001
                print(label, "ERROR", type(exc).__name__, str(exc)[:300])
                if session.info.pop(FAILED_RUN_RECORDED, False):
                    session.commit()
                else:
                    session.rollback()
                continue
            print(label, "outcome:", outcome)
        finally:
            await client.aclose()
    print("stock bars:", _sql(session, "select count(*), min(session_date), max(session_date), count(ingestion_run_id), count(distinct ingestion_run_id), min(price_basis) from daily_price_bar"))
    print("benchmark bars:", _sql(session, "select count(*), min(session_date), max(session_date), count(ingestion_run_id), count(distinct ingestion_run_id) from benchmark_price_bar"))
    print("runs:", _sql(session, "select id, dataset, status, records_received, records_written, records_skipped, records_failed, raw_content_sha256 is not null from ingestion_run order by started_at"))
    print("last stock sessions:", _sql(session, "select session_date, close from daily_price_bar order by session_date desc limit 6"))


async def stage_fsds_download() -> None:
    """ONE SEC request: the 2026q2 FSDS package, streamed to disk with SHA-256."""
    import hashlib

    import httpx2 as httpx

    s = get_settings()
    ua = s.sec_user_agent.get_secret_value()
    url = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q2.zip"
    out = common.ART / "fsds" / "2026q2.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    size = 0
    async with httpx.AsyncClient(transport=common.CountingTransport("sec"), headers={"User-Agent": ua}, timeout=600.0) as c:
        async with c.stream("GET", url) as r:
            print("HTTP status:", r.status_code, "content-length:", r.headers.get("content-length"), "last-modified:", r.headers.get("last-modified"))
            if r.status_code != 200:
                return
            with out.open("wb") as fh:
                async for chunk in r.aiter_bytes(1 << 20):
                    fh.write(chunk)
                    sha.update(chunk)
                    size += len(chunk)
    print("saved bytes:", size, "sha256:", sha.hexdigest())


async def stage_bars_diagnose() -> None:
    """ONE Massive request: re-fetch NKE 2026-10-01..10-06 through the existing client and keep the
    bytes the failed run did not retain. The client's own parser raises; we read the capture."""
    from quantforge.models.enums import PriceBasis
    from quantforge.ingestion.canonical import ResolvedVenue

    s = get_settings()
    client = massive_client(s, common.CountingTransport("massive"))
    try:
        try:
            res = await client.get_daily_bars("NKE", date(2026, 10, 1), date(2026, 10, 6), venue=ResolvedVenue(mic="XNYS", name="New York Stock Exchange", timezone_name="America/New_York"), price_basis=PriceBasis.SPLIT_ADJUSTED)
            print("parsed OK:", len(res.bars))
            caps = res.captures
        except Exception as exc:  # noqa: BLE001
            print("client raised:", type(exc).__name__, str(exc)[:200])
            caps = getattr(exc, "captures", ())
    finally:
        await client.aclose()
    common.raw_store().store_all(caps)
    for cap in caps:
        doc = json.loads(cap.body)
        print({k: doc[k] for k in doc if k != "results"})
        for r in doc.get("results", []):
            print(r)


async def stage_bars_raw() -> None:
    """ONE Massive request: fetch the raw NKE 2026-10-01..10-06 aggregate JSON for INSPECTION only.

    The existing client raises on the malformed record and attaches no capture, so the bytes the
    failed run never kept are fetched once through the same counting transport and auth header.
    Nothing here is parsed into canonical data or ingested.
    """
    import hashlib

    import httpx2 as httpx

    s = get_settings()
    client = massive_client(s, common.CountingTransport("massive"))
    try:
        headers = client._auth_headers()  # noqa: SLF001 - reuse the adapter's own auth wiring
        r = await client._client.get(  # noqa: SLF001
            "/v2/aggs/ticker/NKE/range/1/day/2026-10-01/2026-10-06",
            params={"adjusted": "true", "sort": "asc", "limit": "50"},
            headers=headers,
        )
    finally:
        await client.aclose()
    print("HTTP", r.status_code, "bytes", len(r.content), "sha256", hashlib.sha256(r.content).hexdigest())
    out = common.ART / "raw" / "diagnostic"
    out.mkdir(parents=True, exist_ok=True)
    (out / "nke-2026-10-01_10-06.json").write_bytes(r.content)
    doc = r.json()
    print({k: doc[k] for k in doc if k != "results"})
    for row in doc.get("results", []):
        print(row)


async def stage_bars_anchor() -> None:
    """ONE Massive request: NKE bars from the AMC anchor session (2026-10-02) onward, ingested."""
    from datetime import UTC, datetime

    from quantforge.ingestion.runs import FAILED_RUN_RECORDED
    from quantforge.ingestion.service import IngestionService
    from quantforge.models.enums import PriceBasis

    session, settings = _session()
    client = massive_client(settings, common.CountingTransport("massive"))
    try:
        svc = IngestionService(session=session, provider=client, store=common.raw_store(), now=datetime.now(UTC))
        try:
            outcome = await svc.ingest_daily_bars("NKE", date(2026, 10, 2), date(2026, 10, 6), price_basis=PriceBasis.SPLIT_ADJUSTED)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            print("ERROR", type(exc).__name__, str(exc)[:300])
            if session.info.pop(FAILED_RUN_RECORDED, False):
                session.commit()
            else:
                session.rollback()
            return
        print("outcome:", outcome)
    finally:
        await client.aclose()
    print("stock bars:", _sql(session, "select count(*), min(trading_date), max(trading_date), count(ingestion_run_id), count(distinct ingestion_run_id), min(price_basis) from daily_price_bar"))
    print("runs:", _sql(session, "select dataset, status, records_received, records_written, records_failed, raw_content_sha256 is not null from ingestion_run order by started_at"))
    print("bars:", _sql(session, "select trading_date, close, vwap, ingestion_run_id = (select id from ingestion_run where dataset='daily-bars' and status='succeeded') from daily_price_bar order by 1"))


def stage_fsds_inspect() -> None:
    """Offline inspection of the downloaded package (no network). Candidate discovery only."""
    import csv
    import hashlib
    import io
    import zipfile
    from collections import defaultdict

    from quantforge.providers.sec.fsds import FsdsPackage

    path = common.ART / "fsds" / "2026q2.zip"
    with FsdsPackage(path):  # the production reader opens it: it is a usable SUB+NUM package
        print("FsdsPackage opens the ZIP: yes (no network)")
    z = zipfile.ZipFile(path)
    sub = {}
    with z.open("sub.txt") as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"), delimiter="\t"):
            sub[row["adsh"]] = row
    print("submissions:", len(sub), "forms:", {f: sum(1 for r in sub.values() if r["form"] == f) for f in ("10-Q", "10-K", "10-Q/A", "10-K/A")})
    filed = sorted(r["filed"] for r in sub.values())
    print("filed range:", filed[0], filed[-1])
    eps = defaultdict(list)
    n = 0
    with z.open("num.txt") as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"), delimiter="\t"):
            n += 1
            if row["tag"] == "EarningsPerShareDiluted" and row["version"].startswith("us-gaap/") and row["uom"] == "USD" and not row["segments"] and not row["coreg"] and row["value"] != "":
                eps[row["adsh"]].append((row["ddate"], int(row["qtrs"]), row["value"], row["version"]))
    print("num rows:", n, "accessions with unsegmented USD diluted EPS:", len(eps))

    def months_back(d: str) -> str:
        return f"{int(d[:4]) - 1}{d[4:]}"

    cands = []
    for adsh, rows in eps.items():
        r = sub.get(adsh)
        if r is None or r["form"] not in ("10-Q", "10-K"):
            continue
        qtrs = 1 if r["form"] == "10-Q" else 4
        cur = [x for x in rows if x[0] == r["period"] and x[1] == qtrs]
        if len(cur) != 1:
            continue
        comp = [x for x in rows if x[1] == qtrs and x[0] == months_back(r["period"])]
        cands.append((r, cur[0], comp))
    both = [c for c in cands if len(c[2]) == 1]
    print("10-Q/10-K accessions with exactly one current fact:", len(cands), "with exactly one comparative 12 months earlier (same-day-of-month-end):", len(both))
    for cik in ("0000320187", "0000320193", "320187", "320193"):
        hits = [(r["adsh"], r["form"], r["period"], r["filed"]) for r in sub.values() if r["cik"].lstrip("0") == cik.lstrip("0")]
        print("stored-company CIK", cik, "->", hits)
    import random

    random.seed(11)
    pool_q = [c for c in both if c[0]["form"] == "10-Q"]
    pool_k = [c for c in both if c[0]["form"] == "10-K"]
    picks = random.sample(pool_k, 2) + random.sample(pool_q, 3)
    for r, cur, comp in picks:
        print(r["cik"], r["adsh"], r["form"], r["name"][:40], "period", r["period"], "fp", r["fp"], "filed", r["filed"], "cur", cur[2], cur[3], "comp", comp[0][2])
    print("SHA256", hashlib.sha256(path.read_bytes()).hexdigest())


async def stage_listing_control() -> None:
    """ONE Massive request (negative control): VFS dated snapshot for 2023-06-01, BEFORE the retained
    source-stated list_date 2023-08-15. Corroboration only; nothing is ingested."""
    s = get_settings()
    client = massive_client(s, common.CountingTransport("massive"))
    try:
        try:
            result = await client.get_ticker_snapshots("VFS", date(2023, 6, 1))
        except Exception as exc:  # noqa: BLE001
            print("RESULT: provider error:", type(exc).__name__, str(exc)[:300])
            for c in getattr(exc, "captures", ()):
                print("capture body:", c.body[:300])
            return
    finally:
        await client.aclose()
    common.raw_store().store_all(result.captures)
    print("canonical snapshots:", len(result.snapshots), "unsupported:", result.unsupported, "rejected:", result.rejected)
    for cap in result.captures:
        doc = json.loads(cap.body)
        print({k: doc[k] for k in doc if k != "results"}, "results:", len(doc.get("results", [])))
        for r in doc.get("results", []):
            print(r)


def stage_fsds_validate() -> None:
    """Offline: run the PRODUCTION FSDS reader and EPS selectors over the preserved ZIP (no DB, no network)."""
    import hashlib
    import uuid

    from quantforge.financials.comparative_eps import select_comparative_eps
    from quantforge.financials.eps import EpsInputFact, EpsKind, EpsPeriodClass
    from quantforge.financials.eps_change import compute_eps_change
    from quantforge.providers.sec.fsds import FsdsNumScan, FsdsPackage, FsdsSelection

    path = common.ART / "fsds" / "2026q2.zip"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "d7c815395cd420cfbe09b29dbae73a45406c5d0652d5ae1859ae11ea2e22df77", "SHA mismatch"
    print("package SHA verified")
    adsh = "0000320187-26-000037"
    with FsdsPackage(path) as pkg:
        scan = pkg.scan_submissions(FsdsSelection(accessions=frozenset({adsh})))
        sub = scan.submissions[adsh]
        print("submission:", sub)
        nscan = FsdsNumScan()
        facts = [f for f in pkg.iter_facts(frozenset({adsh}), nscan) if f.tag.startswith("EarningsPerShare")]
    print("EPS facts read by production reader:", len(facts), "rejected rows:", nscan.rejected if hasattr(nscan, "rejected") else None)
    for f in facts:
        print(" ", f.tag, f.taxonomy_version, f.period_end, f.duration_quarters, f.unit, repr(f.segments), repr(f.coreg), f.value)
    inputs = [
        EpsInputFact(fact_id=uuid.uuid4(), accession_number=f.accession_number, tag=f.tag, taxonomy_version=f.taxonomy_version, unit=f.unit,
                     period_end=f.period_end, duration_quarters=f.duration_quarters, value=f.value, segments=f.segments, coreg=f.coreg)
        for f in facts
    ]
    obs = select_comparative_eps(
        accession_number=adsh, company_cik=sub.cik, form=sub.form, submission_period_ends=[sub.period_end],
        facts=inputs, period_class=EpsPeriodClass.QUARTER, eps_kind=EpsKind.DILUTED,
    )
    print("comparative status:", obs.status, "|", obs.reason)
    print("current:", obs.current.status, obs.current.value, obs.current.period_end if hasattr(obs.current, "period_end") else "")
    print("comparative fact:", obs.comparative_fact.period_end, obs.comparative_fact.value, obs.comparative_fact.duration_quarters)
    chg = compute_eps_change(obs)
    print("change:", chg.status, getattr(chg, "eps_change", None), getattr(chg, "transition", None), getattr(chg, "percentage_change", None))


async def stage_listing_ingest() -> None:
    """ONE Massive request: ingest the NKE dated snapshot for 2026-10-01 through the repaired service."""
    from datetime import UTC, datetime

    from quantforge.ingestion.listing_history import ListingHistoryIngestionService
    from quantforge.ingestion.runs import FAILED_RUN_RECORDED

    session, settings = _session()
    client = massive_client(settings, common.CountingTransport("massive"))
    try:
        svc = ListingHistoryIngestionService(session=session, provider=client, store=common.raw_store(), now=datetime.now(UTC))
        try:
            outcome = await svc.ingest_ticker_snapshot("NKE", date(2026, 10, 1))
            session.commit()
        except Exception as exc:  # noqa: BLE001
            print("ERROR", type(exc).__name__, str(exc)[:400])
            if session.info.pop(FAILED_RUN_RECORDED, False):
                session.commit()
            else:
                session.rollback()
            return
        print("outcome:", outcome)
    finally:
        await client.aclose()
    print(_sql(session, "select kind, ticker, effective_date, mic, ingestion_run_id is not null from listing_history_fact"))


async def stage_earnings() -> None:
    """ONE Finnhub request: NKE earnings 2026-09-25..2026-10-02 through the real service."""
    from datetime import UTC, datetime

    from quantforge.ingestion.runs import FAILED_RUN_RECORDED
    from quantforge.ingestion.service import EarningsIngestionService

    session, settings = _session()
    client = finnhub_client(settings, common.CountingTransport("finnhub"))
    try:
        svc = EarningsIngestionService(session=session, provider=client, store=common.raw_store(), now=datetime.now(UTC))
        try:
            outcome = await svc.ingest_earnings("NKE", date(2026, 9, 25), date(2026, 10, 2))
            session.commit()
        except Exception as exc:  # noqa: BLE001
            print("ERROR", type(exc).__name__, str(exc)[:400])
            if session.info.pop(FAILED_RUN_RECORDED, False):
                session.commit()
            else:
                session.rollback()
            return
        print("outcome:", outcome)
    finally:
        await client.aclose()
    print(_sql(session, "select event_date, timing, reported_eps, estimated_eps, observed_at, observed_at_basis, ingestion_run_id is not null from earnings_event"))


async def stage_bars_repaired() -> None:
    """Two Massive requests (stock then SPY benchmark) for 2026-10-01..2026-10-06 through the repaired services."""
    from datetime import UTC, datetime

    from quantforge.ingestion.runs import FAILED_RUN_RECORDED
    from quantforge.ingestion.service import IngestionService
    from quantforge.models.enums import PriceBasis

    start, end = date(2026, 10, 1), date(2026, 10, 6)
    session, settings = _session()
    for label in ("stock", "benchmark"):
        client = massive_client(settings, common.CountingTransport("massive"))
        try:
            svc = IngestionService(session=session, provider=client, store=common.raw_store(), now=datetime.now(UTC))
            try:
                if label == "stock":
                    outcome = await svc.ingest_daily_bars("NKE", start, end, price_basis=PriceBasis.SPLIT_ADJUSTED)
                else:
                    outcome = await svc.ingest_benchmark_daily_bars(
                        "SPY", start, end, name="SPY (ETF proxy for the S&P 500)", price_basis=PriceBasis.SPLIT_ADJUSTED
                    )
                session.commit()
            except Exception as exc:  # noqa: BLE001
                print(label, "ERROR", type(exc).__name__, str(exc)[:400])
                if session.info.pop(FAILED_RUN_RECORDED, False):
                    session.commit()
                else:
                    session.rollback()
                continue
            print(label, "outcome:", outcome)
        finally:
            await client.aclose()
    print("stock bars:", _sql(session, "select trading_date, close, vwap, ingestion_run_id is not null from daily_price_bar order by 1"))
    print("benchmark bars:", _sql(session, "select count(*), min(trading_date), max(trading_date), count(ingestion_run_id) from benchmark_price_bar"))


def stage_observe() -> None:
    """Offline over the validation DB: real assembler, manifest, optional save and verify. No requests."""
    import time

    import sqlalchemy as sa

    from quantforge.models.benchmark import Benchmark
    from quantforge.models.earnings import EarningsEvent
    from quantforge.observation.assembler import assemble_research_observation
    from quantforge.reproducibility.manifest import build_reproducibility_record
    from quantforge.research_records import save_research_record, verify_stored_record

    session, _ = _session()
    event = session.scalars(sa.select(EarningsEvent).where(EarningsEvent.event_date == date(2026, 10, 1))).one()
    bench = session.scalars(sa.select(Benchmark)).one()
    print("event:", event.id, event.event_date, event.timing, "observed_at", event.observed_at)
    for label, bid in (("A stock-only", None), ("B explicit SPY ETF proxy", bench.id)):
        t0 = time.monotonic()
        obs = assemble_research_observation(session, event.id, horizons=(1, 2, 5), benchmark_id=bid)
        t1 = time.monotonic()
        rec = build_reproducibility_record(session, obs)
        t2 = time.monotonic()
        study = obs.descriptive.study
        sa_ = study.alignment.session_alignment
        print(f"== {label} (assemble {t1 - t0:.3f}s, manifest {t2 - t1:.3f}s)")
        print(" listing:", obs.identity.listing_resolution.status, obs.identity.listing_resolution.evidence, "exchange_mic", obs.identity.listing_resolution.exchange_mic, "calendar mic", study.alignment.session_calendar_mic)
        print(" alignment:", sa_.status, "event_session", sa_.event_session, "anchor", sa_.first_post_event_session)
        print(" PIT:", obs.point_in_time.state, [r for r in getattr(obs.point_in_time, "refusals", [])] or "")
        for fr, br, ab in zip(study.stock_returns.forward_returns, study.benchmark_returns, study.abnormal_returns, strict=True):
            print(f"  h{fr.horizon}: stock {fr.status.value} {fr.anchor_session}->{fr.target_session} ret={fr.return_value} | bench {br.status.value} {br.anchor_session}->{br.target_session} ret={br.benchmark_return} | abn {ab.status.value} {getattr(ab, 'abnormal_return', None)}")
        ev = rec.manifest.evidence
        print(" evidence:", {"event": "present", "listing": ev.listing_resolution_status, "pit": ev.pit.eligibility if hasattr(ev.pit, "eligibility") else "pit", "stock": ev.stock.state.value, "benchmark": ev.benchmark.state.value, "filing": ev.filing.state.value, "accounting": ev.accounting.state.value})
        print(" level:", rec.baseline.level.value, "gaps:", [g.value for g in rec.baseline.gaps])
        print(" fingerprints:", rec.baseline.fingerprints.manifest.digest[:16], rec.baseline.result_fingerprint.digest[:16])
    if "--save" in sys.argv:
        t0 = time.monotonic()
        saved = save_research_record(session, event.id, horizons=(1, 2, 5), benchmark_id=bench.id)
        session.commit()
        t1 = time.monotonic()
        report = verify_stored_record(session, saved.record.record_id)
        t2 = time.monotonic()
        print(f"SAVED created={saved.created} id={saved.record.record_id} level={saved.record.baseline.level.value} save {t1 - t0:.3f}s verify {t2 - t1:.3f}s status={report.status.value}")


def main() -> None:
    stage = sys.argv[1]
    if stage == "env":
        stage_env()
    elif stage == "setup":
        common.create_db_and_migrate()
    elif stage == "listing-probe":
        asyncio.run(stage_listing_probe())
    elif stage == "finnhub-depth":
        asyncio.run(stage_finnhub_depth())
    elif stage == "bars-diagnose":
        asyncio.run(stage_bars_diagnose())
    elif stage == "bars-raw":
        asyncio.run(stage_bars_raw())
    elif stage == "bars-anchor":
        asyncio.run(stage_bars_anchor())
    elif stage == "fsds-inspect":
        stage_fsds_inspect()
    elif stage == "listing-control":
        asyncio.run(stage_listing_control())
    elif stage == "fsds-validate":
        stage_fsds_validate()
    elif stage == "drop":
        common.drop_validation_db()
    elif stage == "listing-ingest":
        asyncio.run(stage_listing_ingest())
    elif stage == "earnings":
        asyncio.run(stage_earnings())
    elif stage == "bars-repaired":
        asyncio.run(stage_bars_repaired())
    elif stage == "observe":
        stage_observe()
    elif stage == "reference":
        asyncio.run(stage_reference())
    elif stage == "bars":
        asyncio.run(stage_bars())
    elif stage == "fsds-download":
        asyncio.run(stage_fsds_download())
    else:
        sys.exit(f"unknown stage {stage}")


if __name__ == "__main__":
    main()
