# Step 11.2D — Market-data semantics / provenance repair design

Status: DESIGN ONLY. No production code, migration or data was changed. 11.2E may implement only the
repairs approved here. Evidence base: official Massive documentation and the observed NKE record
(see `docs/production-validation-design.md` section 20), plus a read-only audit of the repository
and of both databases on 2026-10-07. Base commit `dfc64b2`, Alembic head `0013`.

## 0. Frozen 11.2C conclusions
A. The dated-snapshot `date` parameter is a documented point in time, so the request date is a
defensible fact date. B. The contract does not make `primary_exchange` (hence `primary_mic`) or any
other returned metadata as-of that date. C. Massive does not document `low <= vw <= high`; the
containment rule is not source-contract backed. D. A successful response can be lost when later
canonical validation fails. E. One invalid aggregate aborts the whole requested range and nothing
pins that as intended policy.
Accounting candidate preserved for later golden validation (not linked to the October event, not
ingested here): NKE `0000320187-26-000037`, 10-Q, period end 2026-02-28, diluted EPS 0.3500 vs
comparative 0.5400, change -0.1900, ratio -0.3518. FSDS artifact 2026q2: 60,419,016 bytes, SHA-256
`d7c815395cd420cfbe09b29dbae73a45406c5d0652d5ae1859ae11ea2e22df77`; never re-downloaded.

## 1. A new finding that changes the batch policy (critical)
Research defines a "session" as a STORED bar (`research/alignment.py:97-117`, `returns.py:322-346`).
Horizon N targets the N-th stored date after the anchor, and the anchor is the first stored session
on/after (BMO) or strictly after (AMC) the event date (`calendar.py:162-171`). Nothing compares the
stored series to an exchange calendar (`market/calendar.py` is imported only by
`backtest/execution.py`). One missing INTERIOR bar therefore yields status `AVAILABLE` for a return
whose horizon is one session too long, and a missing anchor-day bar silently shifts the anchor;
benchmark returns are computed over the same shifted dates. The test
`tests/test_research_returns.py::test_a_holiday_gap_does_not_shift_the_session_count` pins a
holiday-gap behaviour, and the module docstring says a gap is "never assumed to be a weekend or a
holiday", while the implementation treats every gap as a non-session.
Consequences: (1) this is a pre-existing research honesty gap (a provider legitimately omits a bar
when there were no eligible trades; a partial ingestion window does the same), independent of this
repair; (2) **storing the valid rows of a range that contained a rejected row would manufacture
exactly this silent shift**, so per-record partial storage is unsafe until gap detection exists.
Severity: **P1** (silent wrong horizon/anchor), scheduled as its own design slice (11.2F), not part
of 11.2E.

## 2. Bar model audit (PROVEN)
Path: Massive JSON -> `MassiveDailyBarDTO.from_provider` (`providers/massive/dto.py`) ->
`to_canonical_daily_bar` (`mapper.py:161-232`, finiteness only) -> `CanonicalDailyBar.__post_init__`
(`ingestion/canonical.py:318-368`) -> `DailyBarIngestor` (`ingestion/daily.py`) -> `daily_price_bar`
-> research reads only `close` (`price_series.py:89`; benchmark `benchmark_price_series.py:49`).
Benchmark bars reuse the same canonical type (`ingestion/benchmark.py`, `BENCHMARK_VENUE`) and store
only `close`, so a bad `vw` aborts a SPY ingest although SPY `vw` is never stored.
`low <= vwap <= high` is enforced in exactly ONE place, `canonical.py:357-359`; the DTO and mapper
check type/finiteness; the DB has only `vwap IS NULL OR vwap > 0`; `schemas/market.py` omits vwap.
The only test asserting it: `tests/test_canonical.py::test_vwap_must_sit_inside_the_session_range_too`
(vwap 500 vs 99-105). Positive fixtures with in-range vwap: `test_massive_client.py::test_live_wire_shape_parses_with_whole_share_volume`,
`integration/test_ingestion_daily.py::test_vwap_and_trades_are_stored`. No test pushes an
out-of-range vwap through client, mapper or service.
Nothing reads `vwap`, `volume` or `trades` downstream (grep of `src/` and `alembic/`); a defect in
them cannot change a research number today.

### 2.1 Provider-independent invariants (keep)
finite Decimal; OHLC > 1e-9 and <= 1e8 sanity bounds; `low <= high`; `low <= open <= high`;
`low <= close <= high` (also three times in DB CHECKs and `schemas/market.py`); `volume >= 0`;
`trades >= 0`; tz-aware `observed_at`; valid session date and timestamp range; supported
`price_basis`. Caveat recorded, not acted on: the docs imply `h`/`l` could in principle exclude
prints that `o`/`c` include (extended-hours eligibility), but there is no evidence of it, so OHLC
containment stays.
### 2.2 Provider-specific assumptions (must not masquerade as universal)
`vwap` containment; trade-eligibility behaviour; extended-hours treatment; adjustment semantics of
`vw` and `v` under `adjusted=true` (unverified, README :224); timestamp convention. Only the first is
enforced today.

## 3. VWAP repair (approved for 11.2E)
- **Meaning:** `CanonicalDailyBar.vwap` = *provider-reported volume-weighted average price for the
  aggregate window; the trade population is not specified by the provider and is NOT asserted to
  lie within the bar's high/low*. Weakest truthful meaning (option A). Not regular-session VWAP.
- **Nullability:** unchanged (nullable at DTO, canonical, model and DB). Optional because the
  provider marks it optional; removing containment is not a reason to change it.
- **Replacement validation:** finite (mapper) and positive (existing DB `vwap_positive`;
  mirror in canonical so a bad value fails before the write, not at the constraint). Containment
  removed entirely; no tolerance band, no warning flag (a flag would be an invented semantic).
- **Conflict handling unchanged:** `daily.py:185-203` compares vwap/volume/trades only when both
  sides are non-NULL; that stays.
- **Docs/tests:** replace the canonical comment (:353-357), replace the unit test with
  `vwap outside OHLC is accepted`, keep OHLC rejection tests, add `vwap <= 0` rejected.
- **NKE regression (permanent, offline):** inline payload in the existing test style (MockTransport
  client test plus service-path ingest test) with the observed 2026-10-01 record, exact values
  `o 35.45, h 36.235, l 35.02, c 35.15, vw 34.8298` (volume 112413524.118262, n 560588) plus the
  three following observed sessions (2026-10-02 `o 32.553 h 33.97 l 31.97 c 33.87 vw 33.1565`,
  2026-10-05 `o 33.82 h 34.12 l 32.76 c 33.96 vw 33.4753`, 2026-10-06 `o 33.7 h 34.62 l 33.5
  c 34.61 vw 34.2191`). Assert: client returns all four bars with `vwap == Decimal("34.8298")`;
  ingestion SUCCEEDED 4/4; all rows carry the writing `ingestion_run_id`; the same for the benchmark
  path; research reads closes unchanged. The test never touches Massive.
- **Other cross-field assumptions:** none exist for volume/trades (no volume-vs-price, no trades>0
  rule); none are added.
- **Price basis:** Massive adjusts server-side; all validation runs on the returned values after
  adjustment; QuantForge adjusts nothing. No methodology change.
- **Severity: P1** (undocumented provider assumption enforced as a hard failure that can block the
  anchor bar of a before-market event and aborts the whole run).

## 4. Raw-evidence retention (approved for 11.2E)
### 4.1 Current lifecycle (PROVEN)
Files are written to the filesystem inside the `RunRecorder` block (`record_captures`,
`runs.py:312-355`); the DB row columns `raw_artifact_path/sha/bytes` are set in session memory and
committed later by the CLI. Files are content-addressed, written atomically (temp + fsync + rename),
and are NOT removed by a SAVEPOINT or outer rollback; orphans are possible and tolerated (no GC).
`_record_failure` restores the artifact columns across the savepoint rollback, and `fail()` does not
clear them, so a failed run can point at its manifest. For bars, only `ProviderBasisMismatchError`
carries `exc.captures` out of the client (`client.py:414-418,431-435`); a `ProviderPayloadError` from
`to_canonical_daily_bar` (:450-453), a `ProviderResponseError` such as missing `results`, an error on
page N>1, and an undecodable 200 (`_decode` raises before any capture exists) all lose the bytes.
Other paths: SEC filings/history, listing snapshots/events and splits already retain failed
evidence as `<dataset>-rejected`; reference, earnings and FSDS do not use that convention.
### 4.2 Approved design (option (a))
1. `get_daily_bars`: wrap the page loop and mapping so that `ProviderPayloadError` and
   `ProviderResponseError` get `exc.captures = tuple(captures)` if unset (the pattern already used
   at `client.py:478-485` and `:578-583`). Transport, 5xx and rate-limit errors carry no captures
   (non-200 bodies are never captured).
2. Undecodable 200: attach a `RawCapture` for the undecodable body inside `_get_json` (model:
   `providers/sec/client.py:215-234`).
3. `ingest_daily_bars` and `ingest_benchmark_daily_bars`: widen `except ProviderBasisMismatchError` to
   `except ProviderError` and, when the exception has captures, call `_record_rejected(run,
   exc.captures)` then re-raise. `store_rejected_captures` is best-effort (a store failure never
   masks the primary error; pinned by `test_a_raw_store_failure_while_keeping_evidence_never_masks_the_mismatch`).
4. Failed-run provenance: the existing `raw_artifact_path/sha/bytes` columns point at the manifest of
   the `<dataset>-rejected` artifacts. No schema change; `fail()` is unchanged.
### 4.3 Raw does not mean accepted
Rejected bytes live under `daily-bars-rejected` (never deduplicated onto an accepted path) and the
run is `failed`. The artifact proves only what bytes were received. It implies no canonical
validity, provider correctness, ingestion success, accepted row or PIT eligibility. The failed
run's `error_message` (already redacted/truncated by `_safe_error_text`) carries the first reason.
Optional (decided in 11.2E, code-only): add a small `disposition: "REJECTED"` and the redacted first
reason to the manifest JSON, which is hashed into `raw_content_sha256`.
### 4.4 Immutability and transaction boundary
Unchanged: content-addressed SHA-256, byte length, retrieved_at, source identity, no overwrite
(identical bytes dedup; different bytes coexist; a name/content mismatch raises `RawStoreError`).
Because files are written before the DB commit and survive rollback, no separate commit is needed.
Failure modes: file write fails -> best-effort for rejected evidence, the primary error still
raises; DB fails after files are durable -> orphan files (tolerated). A partial `store()` can leave a
body without sidecar if a parameter is unsafe; not reachable on the bar paths (parameters are
ticker/dates/adjusted).
### 4.5 Scope boundary
Reference and earnings lose bytes on client-parse failure too; those are recorded as deferred
findings (P3), not part of 11.2E.
### 4.6 Security and performance
Sidecars hold provider, dataset, endpoint template, safe request parameters and the body; no
headers, key, URL or query string (`_capture()` stores `dict(request_parameters)` and the body;
`_safe_segment` allowlist; `next_url` never persisted). Retaining a failed 200 body adds no new
exposure class (accepted artifacts already store bodies). Unverified assumption: a provider error
envelope inside a 200 never echoes a credential. Performance: the client already holds every page in
memory; a year of bars is tens of KB; no material change.
Regression: a test that the persisted rejected sidecar/manifest contains neither the key value nor an
Authorization string.

## 5. Per-record versus whole-range policy
Today (PROVEN): the client raises on the first malformed bar (`client.py:423`, `:450-453`);
`DailyBarsResult` has no `rejected` field (unlike splits, snapshots, events); `counts.failed` is
non-zero only on a stored-vs-incoming conflict and immediately raises, so the
`run.partial(...) if counts.failed` branch (`service.py:297,:387`) is unreachable, and the `daily.py`
docstring claim that a malformed record is "counted and skipped" is wrong. No test exercises a
daily/benchmark PARTIAL. CLI treats partial as exit 0 and `error_message` is not set for partial.
| Option | Conservatism | Auditability | Research usability | Idempotency | Complexity | Consistency |
|---|---|---|---|---|---|---|
| A fail whole range, nothing retained | high | low (bytes lost) | none until fixed | n/a | none | matches bars today |
| B store valid rows, reject invalid, PARTIAL | low (see section 1) | medium (counts only) | high but UNSAFE: manufactures silent interior gaps | re-run converges | high: needs coverage + gap detection | matches snapshots/splits |
| C store nothing, retain ALL raw evidence, run FAILED | high | high | none for that range; operator re-runs | trivially safe | low | matches basis-mismatch + SEC |
**Decision: Option C for 11.2E.** The preferred B direction is contradicted by repository evidence:
current structures cannot represent missing rows (no expected-session count, no coverage record,
no reasons in `partial()`), and research cannot detect interior gaps (section 1). Per-record
partial storage is deferred until (i) research gap detection or a persisted coverage record exists
(11.2F) and (ii) partial runs can record reasons. When B is adopted, its semantics must be: invalid
rows never silently dropped; exact rejection count and reasons recorded (manifest first, since a
reason block can be hashed; `request_parameters` writes inside the SAVEPOINT are lost on rollback and
would need `fail()`), raw source retained, valid rows keep run provenance, `PARTIAL` distinguishable
from complete, and no consumer treating `partial` as full coverage (today the reproducibility
manifest checks only that `ingestion_run_id` is non-null, not run status or coverage).
Structured rejection vocabulary (for B, not 11.2E): `INVALID_OHLC`, `INVALID_NUMERIC`,
`MALFORMED_PROVIDER_ROW`, `DUPLICATE_CONFLICT`. `VWAP_OUTSIDE_OHLC` is NOT a rejection after this
repair. Durability: run manifest + immutable raw artifact + structured result is sufficient; no
migration.
Whole-run abort after the VWAP repair now fires only for genuinely malformed rows: **P2**.
### 5.1 Coverage semantics today
Requested range: `request_parameters` (start/end). Returned: `records_received`. Stored:
`records_written`. Identical existing: `records_skipped`. Rejected: `records_failed` (count only,
effectively conflicts). Missing rows: NOT representable. Under Option C a `succeeded` run means every
returned row was stored or already present; it still does not prove the provider returned every
session. That residual gap is the section 1 finding.
### 5.2 Event-day versus anchor
AMC: the event-day session is not the return anchor; the next stored session is. BMO: the event-day
session can be the anchor. A bad event-day bar therefore has different downstream effects by timing;
no timing-specific ingestion validation is created. A window starting at the AMC anchor ingests
cleanly and hides an event-day anomaly (observed 2026-10-07) — a coverage blind spot, not a bug.
### 5.3 Callers
`cli.py:884/:914`, `tests/integration/test_ingestion_cli.py:1101`, `test_ingestion_benchmark.py:528`,
`scripts/validation/probe.py`. Under Option C the failure exit code (1) is unchanged; no public API
changes. A future PARTIAL is exit 0 by design ("success with a shortfall"), which must be revisited
in 11.2F.

## 6. Snapshot fact repair (approved for 11.2E)
Current mapping: `ListingHistoryFact` (`models/listing_history.py`, migration 0010): `ticker`,
`effective_date` (= REQUEST date, `client.py:628`), `kind` TICKER_OBSERVED (only when `active`) and
LAST_TRADED from `delisted_utc`, `mic` (nullable; CHECK `mic IS NULL OR kind='TICKER_OBSERVED'`).
`_write_snapshot` stores `mic=snapshot.primary_mic` (`ingestion/listing_history.py:244`).
- **Date-qualified fact:** ticker availability on the requested date (contract-supported).
- **Not date-qualified by the contract:** `primary_mic`/`primary_exchange`, name, CIK, FIGIs.
- **Usage audit (PROVEN):** the stored fact `mic` is dead data: `FactView.mic` is copied
  (`resolver.py:229`) but never read; `Era` has no mic; resolver `exchange_mic` comes only from the
  stored `Listing` row and only when a first-observed interval covers the date; every consumer
  (research, signals, backtest, earnings API, presenter) traces to `Listing.exchange`. The only
  reader is the idempotency check in `_FactWriter.write` (`:125-139`).
- **Repair (option (c)):** stop persisting the snapshot mic (drop `mic=snapshot.primary_mic` at
  `:244`), remove the now-dead mic-conflict branch in `_FactWriter.write`, keep the canonical field
  (raw sidecar still retains `primary_exchange`), keep the nullable column and CHECK. Fix the
  misleading docstrings (`models/listing_history.py:20-22,:113`, `canonical.py:726-731`).
  Rejected: (a) null in mapper (loses canonical evidence; breaks mapper tests), (b) match-against-
  Listing heuristic (a heuristic), (d) documentation only (leaves a misleading stored claim).
  No invented venue; behaviour-neutral for every downstream consumer.
- **CIK and FIGI:** attribution uses the share-class FIGI only (composite/legacy never anchor); CIK
  is a negative cross-check only and never finds a security (`_attribute`, `ingestion/listing_history.py:162-189`);
  name is unused. These establish the IDENTITY of the returned instrument (as the provider states it
  for that request), not that the identifier was historically effective on the date; a later check
  (`_verify_identity`) requires the resolver to resolve the ticker on the date to the same security,
  guarding ticker reuse. Keep the two concepts separate in docs.
- **Contract provenance, in code, no runtime dependency:** following the existing
  `TICKER_EVENT_DATE_SEMANTICS_VERIFIED` pattern (`mapper.py:333-338`, pinned by
  `tests/test_corporate_action_isolation.py:117-118`), add near the snapshot mapper:
  `SNAPSHOT_DATE_SEMANTICS = "REQUEST_PARAMETER_IS_AS_OF_DATE"`,
  `SNAPSHOT_DATE_STATED_IN_RESPONSE = False`, `SNAPSHOT_ATTRIBUTES_ARE_AS_OF_DATE = False`, with a
  `#:` comment quoting the minimum contract text and referencing this design; reference it from the
  mapper function, `dto.py:336` and `canonical.py:725`; pin the values with tests.
- **list_date:** deferred. Zero uses in `src/tests/alembic`; `MassiveTickerDetailsDTO` ignores it
  (`extra="ignore"`). Add only a regression that it stays unused and ignored. Its semantics across
  ticker changes, relistings and share classes are not defined by the docs.
- **Tests:** request date becomes fact date (new client-level assertion that
  `result.snapshots[0].as_of_date == request date`, and an ingest-level test that does not use a stub
  with its own date); response without a date accepted (existing helpers imply it; add an explicit
  test that a `date` key in the payload is ignored); no backward extension
  (`test_one_snapshot_never_becomes_a_continuous_interval`,
  `test_a_first_observed_listing_alone_proves_nothing_before_its_valid_from`); no gap filling
  (`test_a_gap_between_proven_eras_is_never_filled`); primary_mic not promoted
  (`test_a_sourced_only_resolution_never_reports_a_venue_it_cannot_evidence` plus new
  `fact.mic IS NULL` after a snapshot with a primary exchange); replace
  `test_a_rerun_is_idempotent_and_a_changed_venue_is_an_explicit_conflict` with an idempotent re-run
  that no longer conflicts on venue; adjust `tests/integration/test_listing_history.py:543-549,:455`
  and `tests/test_listing_history_provider.py:119,:124-126` as needed.
- **Severity:** snapshot fact dating **P3** (provenance-documentation); stored snapshot
  `primary_mic` **P3** (misleading but behaviour-neutral, dead data).

## 7. Existing data impact (read-only audit)
- Listing facts: 0 rows in both databases (0 with mic). Detection if rows ever appear:
  `SELECT id, security_id, ticker, effective_date, mic, ingestion_run_id FROM listing_history_fact
  WHERE mic IS NOT NULL;` (join `ingestion_run` on dataset `ticker-snapshots`/`ticker-events`).
  No destructive cleanup: facts are never edited; remediation would be an explicit approved data
  migration after export. Nothing to do today.
- Price bars: 5 dev and 3 validation stock bars, none with vwap outside [low, high]; no stored bar
  could be affected by removing the rule (it only widens acceptance).
- Failed runs: exactly one, in the validation database (`1d74bf23-e7c9-4d23-8428-779b4d3be7df`,
  NKE 2026-09-01..2026-10-06, `ProviderPayloadError`, `raw_artifact_path` NULL). It cannot be repaired
  without bytes; the manually saved diagnostic file is not linked and must not be back-linked.
  Detection predicate: `status='failed' AND raw_artifact_path IS NULL AND error_type IN
  ('ProviderPayloadError','ProviderResponseError')` (optionally restricted by dataset). Reporting
  only; no mechanism needed now.
- Dev DB: two `partial` Finnhub earnings runs with no reasons recorded (known P2/P3 diagnostics gap).

## 8. Test plan
**Unit:** canonical accepts vwap outside OHLC; rejects `vwap <= 0`; invalid OHLC still rejected;
non-finite rejected; client attaches captures to `ProviderPayloadError`/`ProviderResponseError`
including on a later page; undecodable 200 carries its bytes; NKE four-bar regression; snapshot
constants pinned; mapper keeps `primary_mic` in the canonical record; list_date stays ignored.
**Integration:** realistic NKE aggregate through the real service: SUCCEEDED 4/4 with run ids;
benchmark equivalent; an invalid-OHLC row fails the run, stores no bar, and leaves a manifest under
`daily-bars-rejected` whose SHA matches the file; rejected artifact never appears in the accepted
path; run row keeps `raw_artifact_path` after rollback; idempotent rerun (second identical run
skipped, same raw SHA); snapshot event-date resolution with `mic IS NULL`; a research read over the
stored NKE bars shows the typed horizon refusal (+5/+20 `INSUFFICIENT_FORWARD_SESSIONS`).
**Failure paths:** HTTP failure before bytes (no artifact); 200 with malformed JSON (bytes kept);
200 with an invalid row (bytes kept); several invalid rows; all rows invalid; canonical conflict
(accepted manifest stays); artifact persistence failure (primary error unmasked); DB write failure
after durability (orphan tolerated, rerun dedups).
**Reproducibility regression:** normal new event + listing + stock bars + benchmark bars flow into
the manifest with every consumed bar carrying a run id and no manual repair; do not require COMPLETE
when other evidence is legitimately partial.
**Observability:** the run outcome prints status, received/written/skipped/failed, artifact SHA and
manifest path; logs never dump payloads or request URLs.

## 9. Migration decision
**NO MIGRATION.** The artifact columns, run row, manifest and fact structures already express the
corrected semantics; the `mic` column and CHECK stay.

## 10. Severity freeze
VWAP invariant **P1**; failed raw retention (bars/benchmark) **P2**; whole-run abort **P2**;
snapshot fact dating **P3**; snapshot `primary_mic` **P3** (independent: misleading stored
claim, behaviour-neutral, 0 rows); **gap-blind session counting P1 (new, separate slice 11.2F)**;
unreachable daily/benchmark PARTIAL branch and stale docstring **P3**; reference/earnings raw loss
on client-parse failure **P3 (deferred)**.

## 11. Exact proposed 11.2E production diff
1. `ingestion/canonical.py`: delete the vwap containment (:353-359), add `vwap > 0` check, update
   the field comment/docstring to the provider-reported meaning.
2. `providers/massive/client.py`: attach captures to payload/response errors in `get_daily_bars`;
   attach a capture to an undecodable-200 error in `_get_json`.
3. `ingestion/service.py`: `ingest_daily_bars` and `ingest_benchmark_daily_bars` store captures from
   any `ProviderError` that carries them as rejected evidence before re-raising.
4. `ingestion/daily.py` docstring and `README.md`: correct the false partial/"counted and skipped"
   statements; document that Option C is the policy.
5. `ingestion/listing_history.py`: stop persisting snapshot `mic`; remove the dead mic-conflict
   branch. `models/listing_history.py` and `ingestion/canonical.py` docstrings corrected.
6. `providers/massive/mapper.py`: add the three snapshot contract constants with the comment.
7. Tests as in section 8 (new and changed). No migration, no frontend, no research methodology, no
   new provider/endpoint, no `list_date`, no authentication/save, no golden ingestion.
Explicitly out of 11.2E: per-record partial storage, research gap detection, reference/earnings raw
retention, partial-run reasons, manifest rejection block (optional, decided at 11.2E start).

## 12. First 11.2E action
Write the failing NKE regression tests first (client-level four-bar payload and the service-path
ingest asserting SUCCEEDED 4/4 with run ids), watch them fail on the current containment rule, then
remove the rule.


## 13. Implementation notes (Step 11.2E, implemented 2026-10-07)

Status: IMPLEMENTED (uncommitted). Supersedes the 11.2E/11.2F split proposed in section 1/11.
- **Session model.** Anchor and horizon targets come from the exchange calendar
  (`SessionCalendar`; production adapter `exchange_calendars`, XNYS/XNAS/ARCX only), never from
  stored bars. Venue = the stored listing's exchange (the venue of the bars read); resolver
  `exchange_mic` stays None when venue history is unestablished. `research/calendar.py`:
  `align_event_to_sessions(event_date, timing, calendar, mic)` and `proven_sessions_after(...)`;
  `returns.compute_forward_returns(..., forward_sessions=...)` takes the proven sessions as a REQUIRED
  argument. BMO: event day if it is a session, else the next session; AMC: the next session strictly
  after. Unsupported venue / out-of-range => `NO_SESSION_DATA`. Anchor bar absent => `MISSING_ANCHOR_PRICE`.
  Target proven but no bar => `INSUFFICIENT_FORWARD_SESSIONS` with the proven target date, never
  moved (deliberate reuse of the existing status to keep the frozen API/frontend contract; a
  dedicated status is a follow-up that needs a frontend change). An absent interior bar does not
  invalidate a close-to-close return whose two endpoint bars exist. Benchmark returns use the
  stock's own proven dates; a benchmark missing either date is unavailable, never shifted.
- **Methodology.** `session_alignment` = `exchange-calendar-sessions/1`, `forward_returns` =
  `close-to-close-proven-session-returns/1`; calendar release `4.13.2` pinned by a test and a
  runtime guard; residual assumption added. Old records verify `MANIFEST_CHANGED`.
- **Pre-repair proof** (archive of HEAD): +1 after a missing interior bar returned the NEXT stored
  bar as AVAILABLE; a missing anchor bar made the next stored bar the anchor.
- **Old "holiday" test finding.** `test_a_holiday_gap_does_not_shift_the_session_count` called any
  missing date "a holiday" (its example 2026-02-04 is an ordinary Wednesday); it tested a pure
  function over a series it could not tell from a missing bar, i.e. it blessed the bug. Replaced by
  a closed-day test and a missing-bar-on-a-real-session test, plus real-calendar holiday tests
  (Thanksgiving, Good Friday, MLK Day).
- **Query count (1/3/10 horizons):** 9 statements before and after, flat in horizon count (the
  stored-session read was replaced by one listing-venue read); sessions are proven in memory.
- **VWAP, raw evidence, whole-range policy, snapshot primary_mic:** as designed in sections 3-6,
  implemented without migration. Review additions: vwap shares the OHLC plausibility cap;
  undefined mic never prices from stored dates; sessions proven only when prices exist.
- **Not done (deferred):** per-record partial storage, coverage records, reference/earnings raw
  retention, partial-run reasons, dedicated missing-target status.
