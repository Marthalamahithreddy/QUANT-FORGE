# ruff: noqa: F811 - fixtures imported from the observation integration tests
"""Step 10.9B: immutable research records -- save, load, duplicate policy, verify -- on PostgreSQL."""

from __future__ import annotations

import json
import socket
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10Q
from tests.integration.test_reproducibility import QUARTER, _bump, _pin_runs, _run
from tests.integration.test_research_observation import (  # noqa: F401 - fixtures and helpers
    _event,
    _filing,
    _ingest_facts,
    _record_statements,
    market,
)

from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.enums import ReproducibilityGap, ReproducibilityLevel, SecFormType
from quantforge.models.market import DailyPriceBar
from quantforge.models.research_record import ResearchObservationRecord
from quantforge.repositories.research_record import ResearchRecordRepository
from quantforge.reproducibility import VerificationStatus, verify_rerun
from quantforge.reproducibility.baseline import ReproducibilityBaseline
from quantforge.reproducibility.canonical import canonical_text, fingerprint_of_text
from quantforge.reproducibility.manifest import (
    EvidenceChangedDuringAssemblyError,
    build_reproducibility_record,
)
from quantforge.reproducibility.methodology import current_methodology
from quantforge.reproducibility.verifier import assemble_record
from quantforge.research_records import (
    RecordIntegrityError,
    ResearchRecordNotFoundError,
    ResultDivergenceError,
    load_research_record,
    save_research_record,
    verify_stored_record,
)

pytestmark = pytest.mark.integration

ONE = [1]


def _complete_event(db_session: Session, market: dict[str, object]) -> Any:
    event = _event(db_session, market)
    _pin_runs(db_session, event)
    return event


def _rows(db_session: Session) -> list[ResearchObservationRecord]:
    return list(db_session.scalars(sa.select(ResearchObservationRecord)))


def test_a_complete_record_round_trips_with_exact_canonical_content(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    saved = save_research_record(db_session, event.id, horizons=ONE)
    db_session.commit()
    assert saved.created is True
    row = _rows(db_session)[0]
    assert saved.record.baseline.level is ReproducibilityLevel.COMPLETE
    assert (
        row.reproducibility_gaps == []
        and row.reproducibility_level is ReproducibilityLevel.COMPLETE
    )

    db_session.expire_all()
    loaded = load_research_record(db_session, saved.record.record_id)
    assert loaded.baseline == saved.record.baseline
    assert loaded.baseline.canonical_manifest == row.canonical_manifest
    assert fingerprint_of_text(row.canonical_manifest).digest == row.manifest_fingerprint
    assert fingerprint_of_text(row.canonical_result).digest == row.result_fingerprint
    # the record has its OWN identity, distinct from both fingerprints
    assert str(row.id) not in (row.manifest_fingerprint, row.result_fingerprint)
    with pytest.raises(ResearchRecordNotFoundError):
        load_research_record(db_session, uuid.uuid4())


def test_a_partial_record_is_saved_and_reloaded_with_its_gaps(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)  # no ingestion runs: PARTIAL
    bench = market["benchmark"]
    assert isinstance(bench, Benchmark)
    saved = save_research_record(db_session, event.id, horizons=ONE, benchmark_id=bench.id)
    db_session.commit()
    assert saved.record.baseline.level is ReproducibilityLevel.PARTIAL
    db_session.expire_all()
    loaded = load_research_record(db_session, saved.record.record_id)
    assert set(loaded.baseline.gaps) == {
        ReproducibilityGap.EVENT_WITHOUT_INGESTION_RUN,
        ReproducibilityGap.STOCK_BAR_WITHOUT_INGESTION_RUN,
        ReproducibilityGap.BENCHMARK_BARS_WITHOUT_INGESTION_RUN,
    }
    assert _rows(db_session)[0].reproducibility_level is ReproducibilityLevel.PARTIAL


def test_exact_duplicate_save_is_idempotent_and_created_at_is_not_in_any_fingerprint(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    first = save_research_record(db_session, event.id, horizons=ONE)
    db_session.commit()
    second = save_research_record(db_session, event.id, horizons=ONE)
    db_session.commit()
    assert (first.created, second.created) == (True, False)
    assert second.record.record_id == first.record.record_id
    assert len(_rows(db_session)) == 1
    row = _rows(db_session)[0]
    assert "created_at" not in row.canonical_manifest and "created_at" not in row.canonical_result
    assert second.record.created_at == first.record.created_at


def test_two_different_requests_are_two_records(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    one = save_research_record(db_session, event.id, horizons=[1])
    two = save_research_record(db_session, event.id, horizons=[1, 2])
    assert one.record.record_id != two.record.record_id
    assert one.record.baseline.fingerprints.manifest != two.record.baseline.fingerprints.manifest


def test_the_same_manifest_with_a_different_result_is_surfaced_not_collapsed(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    first = save_research_record(db_session, event.id, horizons=ONE)
    row = db_session.get(ResearchObservationRecord, first.record.record_id)
    assert row is not None
    # a stored baseline whose manifest matches but whose recorded result differs
    altered = json.loads(row.canonical_result)
    altered["event"]["reported_eps"] = "9.9999"
    other = ResearchRecordRepository(db_session).insert_if_absent(
        {
            "manifest_fingerprint": row.manifest_fingerprint,
            "manifest_hash_algorithm": "sha256",
            "canonical_manifest": row.canonical_manifest,
            "result_fingerprint": fingerprint_of_text(canonical_text(altered)).digest,
            "result_hash_algorithm": "sha256",
            "canonical_result": canonical_text(altered),
            "reproducibility_level": row.reproducibility_level,
            "reproducibility_gaps": row.reproducibility_gaps,
        }
    )
    assert other is not None
    # the exact current pair still resolves idempotently to the matching record...
    assert save_research_record(db_session, event.id, horizons=ONE).created is False
    # ...but if ONLY a divergent baseline is stored for the manifest, saving surfaces it
    db_session.execute(sa.text("ALTER TABLE research_observation_record DISABLE TRIGGER USER"))
    db_session.execute(
        sa.delete(ResearchObservationRecord).where(ResearchObservationRecord.id == row.id)
    )
    db_session.execute(sa.text("ALTER TABLE research_observation_record ENABLE TRIGGER USER"))
    with pytest.raises(ResultDivergenceError) as caught:
        save_research_record(db_session, event.id, horizons=ONE)
    assert caught.value.record_id == other
    assert caught.value.stored_result_fingerprint != caught.value.current_result_fingerprint
    assert len(_rows(db_session)) == 1, "nothing was saved"


def test_same_manifest_fingerprint_with_different_canonical_content_is_a_hard_error(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    first = save_research_record(db_session, event.id, horizons=ONE)
    db_session.execute(sa.text("ALTER TABLE research_observation_record DISABLE TRIGGER USER"))
    db_session.execute(
        sa.update(ResearchObservationRecord).values(
            canonical_manifest=ResearchObservationRecord.canonical_manifest + " "
        )
    )
    db_session.execute(sa.text("ALTER TABLE research_observation_record ENABLE TRIGGER USER"))
    with pytest.raises(RecordIntegrityError, match=r"not in canonical form|hash"):
        save_research_record(db_session, event.id, horizons=ONE)
    assert first.created


@pytest.mark.parametrize(
    "tamper",
    ["manifest_text", "result_text", "manifest_digest", "level", "gaps"],
)
def test_tampered_stored_content_fails_loudly_on_load(
    db_session: Session, market: dict[str, object], tamper: str
) -> None:
    event = _complete_event(db_session, market)
    saved = save_research_record(db_session, event.id, horizons=ONE)
    record_id = saved.record.record_id
    changes: dict[str, str] = {
        "manifest_text": "canonical_manifest = canonical_manifest || ' '",
        "result_text": "canonical_result = canonical_result || ' '",
        "manifest_digest": "manifest_fingerprint = repeat('0', 64)",
        "level": "reproducibility_level = 'PARTIAL', reproducibility_gaps = '[\"X\"]'::jsonb",
        "gaps": "reproducibility_level = 'COMPLETE', reproducibility_gaps = '[]'::jsonb",
    }
    db_session.execute(sa.text("ALTER TABLE research_observation_record DISABLE TRIGGER USER"))
    if tamper == "gaps":
        # claim COMPLETE for a PARTIAL manifest: needs a PARTIAL baseline first
        db_session.execute(sa.text("DELETE FROM research_observation_record"))
        db_session.execute(sa.text("ALTER TABLE research_observation_record ENABLE TRIGGER USER"))
        partial_event = _event(db_session, market, fiscal_quarter=2)
        record_id = save_research_record(
            db_session, partial_event.id, horizons=ONE
        ).record.record_id
        db_session.execute(sa.text("ALTER TABLE research_observation_record DISABLE TRIGGER USER"))
    db_session.execute(sa.text(f"UPDATE research_observation_record SET {changes[tamper]}"))
    db_session.execute(sa.text("ALTER TABLE research_observation_record ENABLE TRIGGER USER"))
    db_session.expire_all()
    with pytest.raises(RecordIntegrityError):
        load_research_record(db_session, record_id)


def test_records_are_immutable_in_the_database_and_have_no_mutating_repository_api(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    save_research_record(db_session, event.id, horizons=ONE)
    db_session.commit()
    with pytest.raises(sa.exc.DBAPIError, match="immutable"):
        db_session.execute(sa.text("UPDATE research_observation_record SET canonical_result = ''"))
    db_session.rollback()
    with pytest.raises(sa.exc.DBAPIError, match="immutable"):
        db_session.execute(sa.text("DELETE FROM research_observation_record"))
    db_session.rollback()
    public = {n for n in dir(ResearchRecordRepository) if not n.startswith("_")}
    assert public == {"insert_if_absent", "get", "list_page", "for_manifest", "for_pair"}


def test_a_concurrent_identical_save_resolves_to_the_winner_without_error(
    db_session: Session, market: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _complete_event(db_session, market)
    first = save_research_record(db_session, event.id, horizons=ONE)
    # simulate the race: this saver's duplicate pre-check saw nothing, then lost the INSERT
    monkeypatch.setattr(ResearchRecordRepository, "for_manifest", lambda *_a, **_k: [])
    second = save_research_record(db_session, event.id, horizons=ONE)
    assert second.created is False and second.record.record_id == first.record.record_id
    assert len(_rows(db_session)) == 1


def test_evidence_changing_during_a_save_is_detected_and_nothing_is_written(
    db_session: Session, market: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _complete_event(db_session, market)
    real = assemble_record
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        record = real(*args, **kwargs)
        if calls["n"] == 1:  # the first read, then a consumed bar changes before the second
            _bump(
                db_session,
                DailyPriceBar,
                listing_id=record.manifest.evidence.stock.listing_id,
                trading_date=record.manifest.evidence.stock.bars[1].session,
            )
        return record

    monkeypatch.setattr("quantforge.research_records.service.assemble_record", flaky)
    with pytest.raises(EvidenceChangedDuringAssemblyError):
        save_research_record(db_session, event.id, horizons=ONE)
    assert _rows(db_session) == []


def test_verify_match_for_a_complete_baseline_and_incomplete_for_a_partial_one(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    complete = save_research_record(db_session, event.id, horizons=ONE)
    report = verify_stored_record(db_session, complete.record.record_id)
    assert report.status is VerificationStatus.MATCH
    assert report.baseline == "SUPPLIED", "a stored baseline, not a same-session double run"

    bench = market["benchmark"]
    assert isinstance(bench, Benchmark)
    partial = save_research_record(db_session, event.id, horizons=ONE, benchmark_id=bench.id)
    again = verify_stored_record(db_session, partial.record.record_id)
    assert again.status is VerificationStatus.EVIDENCE_INCOMPLETE
    assert again.level is ReproducibilityLevel.PARTIAL


def test_a_run_stamped_benchmark_baseline_verifies_as_a_complete_match(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    run = _run(db_session)
    db_session.execute(sa.update(BenchmarkPriceBar).values(ingestion_run_id=run.id))
    db_session.commit()
    bench = market["benchmark"]
    assert isinstance(bench, Benchmark)
    saved = save_research_record(db_session, event.id, horizons=ONE, benchmark_id=bench.id)
    assert saved.record.baseline.level is ReproducibilityLevel.COMPLETE
    assert (
        verify_stored_record(db_session, saved.record.record_id).status is VerificationStatus.MATCH
    )


def test_verify_reports_manifest_changed_with_paths_when_consumed_evidence_changes(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    saved = save_research_record(db_session, event.id, horizons=ONE)
    db_session.commit()
    stock = saved.record.baseline.manifest_value["evidence"]["stock"]
    _bump(
        db_session,
        DailyPriceBar,
        listing_id=uuid.UUID(stock["listing_id"]),
        trading_date=datetime.fromisoformat(stock["bars"][1]["session"]).date(),
    )
    report = verify_stored_record(db_session, saved.record.record_id)
    assert report.status is VerificationStatus.MANIFEST_CHANGED
    assert report.changed_sections == ("evidence",)
    assert any(p.startswith("evidence.stock.bars[1].close") for p in report.changed_paths)
    assert report.changed_result_paths, "the result differences are explained by path too"
    # the stored baseline was not touched by verification
    row = db_session.get(ResearchObservationRecord, saved.record.record_id)
    assert (
        row is not None
        and row.manifest_fingerprint == saved.record.baseline.fingerprints.manifest.digest
    )


def test_verify_reports_manifest_changed_for_a_methodology_change(
    db_session: Session, market: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _complete_event(db_session, market)
    saved = save_research_record(db_session, event.id, horizons=ONE)
    monkeypatch.setitem(
        build_reproducibility_record.__kwdefaults__,  # type: ignore[arg-type]
        "methodology",
        current_methodology({"forward_returns": "close-to-close-session-returns/2"}),
    )
    report = verify_stored_record(db_session, saved.record.record_id)
    assert report.status is VerificationStatus.MANIFEST_CHANGED
    assert report.changed_sections == ("methodology",)


def test_verify_reports_result_changed_when_only_the_stored_result_differs(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    saved = save_research_record(db_session, event.id, horizons=ONE)
    row = db_session.get(ResearchObservationRecord, saved.record.record_id)
    assert row is not None
    altered = json.loads(row.canonical_result)
    altered["event"]["reported_eps"] = "9.9999"
    baseline = ReproducibilityBaseline.from_canonical(
        row.canonical_manifest, canonical_text(altered)
    )
    report = verify_rerun(db_session, event.id, horizons=ONE, expected=baseline)
    assert report.status is VerificationStatus.RESULT_CHANGED
    assert report.changed_result_paths == ("event.reported_eps",)
    assert report.changed_sections == ()


def test_unused_data_change_after_saving_does_not_change_the_manifest(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    event = _complete_event(db_session, market)
    saved = save_research_record(db_session, event.id, horizons=ONE)
    db_session.commit()
    sessions = market["sessions"]
    assert isinstance(sessions, list)
    stock = saved.record.baseline.manifest_value["evidence"]["stock"]
    _bump(
        db_session,
        DailyPriceBar,
        listing_id=uuid.UUID(stock["listing_id"]),
        trading_date=sessions[3],
    )
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _event(db_session, market, fiscal_quarter=4)
    report = verify_stored_record(db_session, saved.record.record_id)
    assert report.status is VerificationStatus.MATCH


def test_save_load_verify_use_no_network_and_only_the_expected_statements(
    db_session: Session, market: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _complete_event(db_session, market)

    def tripwire(*_a: object, **_k: object) -> None:
        raise AssertionError("network access")

    monkeypatch.setattr(socket.socket, "connect", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)
    allowed_reads = ("SELECT", "WITH", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")

    with _record_statements(db_session) as save:
        saved = save_research_record(db_session, event.id, horizons=ONE)
    inserts = [s for s in save if s.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1 and "research_observation_record" in inserts[0]
    assert all(s.lstrip().upper().startswith(("INSERT", *allowed_reads)) for s in save)

    db_session.expire_all()
    with _record_statements(db_session) as load:
        load_research_record(db_session, saved.record.record_id)
    assert load and all(s.lstrip().upper().startswith(allowed_reads) for s in load)

    db_session.expire_all()
    with _record_statements(db_session) as verify:
        verify_stored_record(db_session, saved.record.record_id)
    assert verify and all(s.lstrip().upper().startswith(allowed_reads) for s in verify)
    print(f"save={len(save)} load={len(load)} verify={len(verify)}")  # noqa: T201


def test_stored_content_has_no_credentials_contact_identity_or_raw_headers_and_is_small(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    event = _event(db_session, market, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC))
    _pin_runs(db_session, event)
    bench = market["benchmark"]
    assert isinstance(bench, Benchmark)
    saved = save_research_record(
        db_session,
        event.id,
        horizons=[1, 2, 3],
        benchmark_id=bench.id,
        filing_accession=ACC_10Q,
        **QUARTER,
    )
    row = db_session.get(ResearchObservationRecord, saved.record.record_id)
    assert row is not None
    blob = (row.canonical_manifest + row.canonical_result).lower()
    for token in (
        "user-agent",
        "useragent",
        "@",
        "password",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "qf_",
        ".env",
        "http",
    ):
        assert token not in blob, token
    sizes = (len(row.canonical_manifest), len(row.canonical_result))
    print(f"canonical sizes: manifest={sizes[0]} result={sizes[1]} bytes")  # noqa: T201
    assert max(sizes) < 100_000, "no compression needed at these sizes"


def test_an_exact_match_anywhere_among_same_manifest_records_wins(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    first = save_research_record(db_session, event.id, horizons=ONE)
    row = db_session.get(ResearchObservationRecord, first.record.record_id)
    assert row is not None
    altered = json.loads(row.canonical_result)
    altered["event"]["reported_eps"] = "9.9999"
    older = ResearchRecordRepository(db_session).insert_if_absent(
        {
            "manifest_fingerprint": row.manifest_fingerprint,
            "manifest_hash_algorithm": "sha256",
            "canonical_manifest": row.canonical_manifest,
            "result_fingerprint": fingerprint_of_text(canonical_text(altered)).digest,
            "result_hash_algorithm": "sha256",
            "canonical_result": canonical_text(altered),
            "reproducibility_level": row.reproducibility_level,
            "reproducibility_gaps": row.reproducibility_gaps,
        }
    )
    assert older is not None
    again = save_research_record(db_session, event.id, horizons=ONE)
    assert again.created is False and again.record.record_id == first.record.record_id


def test_save_requires_a_clean_session(db_session: Session, market: dict[str, object]) -> None:
    event = _complete_event(db_session, market)
    event.estimated_eps = Decimal("0.5")  # pending change
    with pytest.raises(ValueError, match="clean session"):
        save_research_record(db_session, event.id, horizons=ONE)
    db_session.rollback()


@pytest.mark.parametrize(
    ("manifest", "result"),
    [
        ('{"request":{},"methodology":{},"evidence":{"x":1.5}}', "{}"),
        ('{"request":{},"methodology":{},"evidence":{}}', "{}"),
        ('{"request":{},"methodology":{},"evidence":[]}', "{}"),
        ("[]", "{}"),
        ("not json", "{}"),
    ],
)
def test_malformed_stored_text_is_a_record_integrity_error_not_a_raw_exception(
    db_session: Session, market: dict[str, object], manifest: str, result: str
) -> None:
    event = _complete_event(db_session, market)
    saved = save_research_record(db_session, event.id, horizons=ONE)
    db_session.execute(sa.text("ALTER TABLE research_observation_record DISABLE TRIGGER USER"))
    db_session.execute(
        sa.update(ResearchObservationRecord).values(
            canonical_manifest=manifest, canonical_result=result
        )
    )
    db_session.execute(sa.text("ALTER TABLE research_observation_record ENABLE TRIGGER USER"))
    db_session.expire_all()
    with pytest.raises(RecordIntegrityError):
        load_research_record(db_session, saved.record.record_id)
