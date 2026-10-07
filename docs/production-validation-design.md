# Step 11.2A / 11.2A.1 — Production data validation: design and static preflight

Status: DESIGN ONLY. Nothing here has been executed against a provider. No provider was called, no
data ingested, no record saved, no production code or migration changed. 11.2B requires explicit
human approval of this document and of the exact provider calls in section 9.

Base: Frontend V2 frozen at `c9d940e` (`feat: complete frontend v2`, parent `c2b9ce5`); Alembic head
`0013`. Evidence date 2026-10-07. Labels: PROVEN (read in code/tests/DB/files), DOCS-CITED
(provider claim retained in repository docs, never verified live), UNKNOWN.

## 1. Purpose

Prove that a very small number of real cases traverse SOURCE -> RAW ARTIFACT -> INGESTION RUN ->
CANONICAL ROW -> EVENT -> PRICES -> FILING/ACCOUNTING -> OBSERVATION -> MANIFEST -> IMMUTABLE RECORD
-> VERIFY -> FRONTEND correctly, conservatively and reproducibly. Not volume, not coverage.
Public Save stays blocked on a separate ownership/authorization design. Frontend stays frozen.

## 2. What the repository can do (corrected)

Every network ingest runs through `RunRecorder` (durable failed run, SAVEPOINT domain writes) and
stores raw bytes content-addressed with a sidecar and a per-run manifest. A partial run exits 0.

| Capability | Entry point | Idempotency / conflict | Replay class |
|---|---|---|---|
| Reference | CLI `reference` | get-or-create by CIK/FIGI | C |
| Listing snapshot / events | CLI `listing-snapshot`, `listing-events`, `listing-eras` (read-only) | single-date facts; contradictions fail the run | B |
| Earnings (Finnhub) | **Python only** `EarningsIngestionService` (no CLI) | unique (source, company, fingerprint); revision = new row | C |
| Daily bars | CLI `daily` | unique key; differing bar = `ProviderDataConflict` | C |
| Benchmark bars | CLI `benchmark ... --name` | identical level skipped, **keeps original run id (or NULL)** | C |
| SEC recent / history | CLI `sec-filings`, `sec-filings-history` | unique (company, accession); metadata change = conflict | B |
| Splits | CLI `stock-splits` | n/a here | B |
| FSDS | CLI `sec-fsds PKG.zip` (local file) | unique fact identity; new SHA same name = refused | **A** |
| EPS, observation, record save/verify | derived from stored rows | save: same manifest+result = `created=False` | D |

Replay classes: A existing offline replay path; B network ingestion only (no retained artifact);
C raw artifact exists locally but **no production replay entry point**; D not applicable.
**Only FSDS has a production offline path.** `RawResponseStore.read()` has no production caller and
no ingestor accepts retained bytes. Earlier drafts of this design implied generic replay; that
claim is withdrawn. (Section 11 describes a harness-level technique that is not a production path.)

## 3. Configuration readiness (presence only)

Massive key, Finnhub key, SEC User-Agent, database URL: CONFIGURED (non-empty). Redis: CONFIGURED.
Presence is not entitlement; no value was read or printed.

## 4. Provider state (credential vs endpoint vs live evidence vs entitlement)

| Capability | Endpoint implemented | Live evidence retained | Known entitlement failure | Entitlement for 11.2B |
|---|---|---|---|---|
| Massive reference | yes | AAPL, NKE, VFS | none | works |
| Massive daily bars (`adjusted=true`) | yes | AAPL 2026-09-14..18; SPY 2026-09-21..25 | none | works for ~3-week-old windows; **depth beyond that UNKNOWN** |
| Massive dated listing snapshot | yes | none retained | none | **UNKNOWN** (no live call ever) |
| Massive splits, ticker events | yes | none | none | UNKNOWN (events endpoint experimental) |
| Massive/Benzinga earnings | no mapper | 403 recorded | **403 not entitled** | not usable |
| Massive `I:SPX` | n/a | n/a | 403 recorded (HANDOFF) | not usable; benchmark is SPY |
| Finnhub `/calendar/earnings` | yes (library only) | NKE (2 windows), VFS | none | works for windows starting 2026-09-01; **older history UNKNOWN** |
| SEC submissions / history | yes | earlier probes by scratch scripts; nothing retained, `sec_filing` empty | none | public; UA configured |
| SEC FSDS | yes (local ZIP) | 2026q1 validated, ZIP deleted (SHA `d18c01c6…`, 85,259,424 B, historical) | none | public |

Finnhub code capability (PROVEN): one non-paginated `GET /calendar/earnings` with `symbol`, `from`,
`to`; key sent as a header; the only bound is `end >= start`; the code assumes nothing about
historical reach. "Free tier = 1 month of calendar history" is DOCS-CITED in HANDOFF (source report
no longer retained) and was never verified live; the oldest retained request starts 2026-09-01.
It is therefore NOT a validation rule here.

## 5. Database baseline (read-only, 2026-10-07) and preservation

Existing rows that MUST be preserved untouched: 3 companies/securities/listings (AAPL valid_from
2026-09-28; NKE and VFS 2026-10-02), 2 `data_source`, 2 earnings events (VFS 2026-10-02 UNKNOWN
timing; NKE 2026-12-16 estimate-only), 5 AAPL bars, 5 SPY bars (**NULL `ingestion_run_id`**), 11
`ingestion_run` rows and all `data/raw` files. Empty today: `listing_history_fact`, `sec_filing`,
`financial_fact*`, `stock_split`, `research_observation_record`.
All existing rows look like real provider captures (circumstantial; no fixtures found).
New rows 11.2B would introduce are listed per case in section 9. Recommended before 11.2B:
a `pg_dump` of the dev DB (human-run; not done here).

## 6. Proven findings that reshape the corpus

1. **Listing identity (PROVEN).** One `TICKER_OBSERVED` fact at date T proves only T (a one-day
   era); it cannot resolve an event on E < T. An observation dated exactly on E does resolve E
   (`SOURCE_STATED_HISTORY`). Only a `TICKER_STARTED` chain extends history backward; two
   observations never fill the gap; `LAST_TRADED` never creates an era.
   `Listing.valid_from` is first-observed (reference ingestion sets today's date, never lowered);
   listing-snapshot ingestion never touches `Listing` rows. A source `list_date` exists in retained
   Massive payloads (NKE 1980-12-02) but the code drops it; it is not used. With a fact on E, the
   resolver falls back to the stored NKE Listing row as the ticker's only listing, so research
   gets a `listing_id` with "venue on that date is not established" (resolver.py:186-203).
2. **NKE's incomplete run (PROVEN by code plus DB state; not a recorded fact).** Both partial NKE
   Finnhub runs (`b0a78fc4…` window 2026-09-25..10-02; `7724ba69…` window 2026-09-01..12-31)
   have `error_type`/`error_message` NULL; `partial()` records no reason anywhere (a `logger.warning`
   on stderr only). The failed record is the NKE event dated **2026-10-01, AMC, epsActual 0.48 /
   epsEstimate 0.4364** (identified by comparing the retained payloads to stored rows). Only
   `UnknownIssuerError` can increment `records_failed` on that path, and the only NKE listing has
   `valid_from` 2026-10-02, so 2026-10-01 has no listing evidence. Caveat: assumes the listing
   state at run time equalled today's.
3. **Horizon mechanics (PROVEN).** The anchor is the first stored session on/after the event date
   (BEFORE_MARKET) or strictly after it (AFTER_MARKET), within +/-10 calendar days. Horizon N targets
   the N-th stored session strictly after the anchor: horizon 20 needs the anchor bar plus 20 more
   stored sessions. "Session" means a stored bar. Missing data gives typed statuses
   (`MISSING_ANCHOR_PRICE`, `INSUFFICIENT_FORWARD_SESSIONS` per horizon). UNKNOWN timing refuses
   alignment, so every return is `NO_ALIGNMENT`. Horizons are caller-chosen (1..252, at most 10).
4. **PIT (PROVEN for the planned path).** The Finnhub mapper hard-codes `observed_at=None`
   (`mapper.py:81`); only `EarningsIngestor` writes events and passes values through; no Benzinga
   mapper exists; SEC mapper creates filings only. Eligibility derives from the event's
   `observed_at`/basis alone (never the filing). SOURCE_PUBLICATION cannot occur on any current
   provider path.
5. **Benchmark provenance (PROVEN).** Benchmark evidence is PINNED when a benchmark is requested
   and at least one stock forward return is AVAILABLE; pins are the bars at AVAILABLE anchor/target
   sessions. Gap `BENCHMARK_BARS_WITHOUT_INGESTION_RUN` fires when any pinned benchmark bar has a
   NULL run. New benchmark bars carry the writing run (migration 0013, tested). An identical level
   under another run is skipped and keeps its original run id, so the five legacy SPY bars stay
   NULL forever. Trap: a requested benchmark with zero consumed bars is COMPLETE vacuously.
   **Composed path unproven**: tests prove each link; the COMPLETE tests stamp run ids with SQL
   update, not through real ingestion.
6. **Run-id coverage (PROVEN).** Finnhub service-path events and Massive daily bars carry
   `ingestion_run_id` (DB confirms for existing rows).
7. **Retained SPY artifacts.** The two benchmark runs have different raw SHAs for the same 713-byte
   response; a field-level diff shows only `request_id` differs. Not a provenance defect.

## 7. FSDS and accounting (PROVEN / UNKNOWN)

**NO LOCAL FSDS PACKAGE FOUND** (searched repo, data, Downloads, Desktop, Documents, /tmp, scratch
dirs). The 2026q1 ZIP was deleted after analysis; only its SHA and size survive. No local accession
evidence exists, so no accounting candidate can be listed from local evidence. 2026q2 is the latest
quarter the repository records (docs: Last-Modified 2026-08-19, 60,419,016 B; range-sampled only,
never ingested); 2026q3 is not mentioned anywhere (UNKNOWN). Filings dated after the Q2 boundary
are not in any package the repository knows. A recent event's 10-Q therefore cannot be bound to
FSDS facts, and an observation must never link an event to an accession of a different fiscal
period. **Accounting validation is filing-level** (`GET /filings/{acc}/eps`, `/filings/[acc]`).

AAPL: **UNPROVEN CANDIDATE** (stored CIK/listing only; `sec_filing` empty; no retained evidence of an
AAPL accession in any package; Apple's 52/53-week rounding risk untested on real data).

## 8. Corpus reassessment (final)

| Case | Verdict | Reason |
|---|---|---|
| G1 NKE identity + descriptive, Configuration A: stock-only (event 2026-10-01 AMC, reported) | **APPROVED CANDIDATE** (conditional on Massive dated-snapshot entitlement, UNKNOWN) | Proven failure mode; fixed only by a sourced fact on the event date; real reported event already seen in retained Finnhub bytes |
| G2 Configuration B: the SAME event + explicit SPY ETF-proxy benchmark | **APPROVED CANDIDATE; COMPLETE TARGET — NOT YET PROVEN** | All components can carry run ids on the planned path (event, stock bars, new SPY bars); composed path untested; must have an AVAILABLE return to avoid vacuous COMPLETE |
| G3 filing/accounting | **CONDITIONAL** | Needs one approved SEC FSDS download and offline inspection before any accession is chosen; AAPL not locked; stored companies (NKE, AAPL) are the only candidates considered, to be verified offline |
| G4 forced PARTIAL | **REJECT / REMOVED** | No naturally PARTIAL real case exists (proven); PARTIAL is covered by captured fixtures and integration tests; no legacy-data engineering |
| G5 horizon +20 | **CONDITIONAL, optional** | Cannot be satisfied by the NKE 2026-10-01 event before the 20th stored session after 2026-10-02 (about 2026-10-30, assuming no holiday); needs an older event; enabled only if one discovery request shows older Finnhub history and Massive serves the bars |

G1 and G2 are ONE golden real-world event (NKE 2026-10-01, after market close) studied in TWO research
configurations; they are not two independent real-world validations. Minimum acceptable live corpus:
that one event, two observations. G3 and G5 are added only if
their gating request succeeds. VFS (UNKNOWN timing, no bars) stays a zero-cost refusal reference
and is not ingested further.

+20 is classified OPTIONAL IF PROVIDER HISTORY SUPPORTS IT. It is not weakened silently: the NKE
case demonstrates the typed insufficient-sessions refusal for horizons 5/20 as they truly are.

## 9. Provider-call plan for 11.2B (not executed)

| # | Provider | Purpose | Case | Max | Expected artifact | Assumption |
|---|---|---|---|---|---|---|
| 1 | Massive | `listing-snapshot NKE --date 2026-10-01` | G1 | 1 | 1 snapshot raw + 1 `listing_history_fact` | dated-snapshot entitlement (UNKNOWN) |
| 2 | Finnhub | NKE earnings, window 2026-09-25..2026-10-02 (a real call so provenance is a real retrieval) | G1 | 1 | event 2026-10-01 stored with run id | key valid |
| 3 | Massive | NKE daily bars 2026-10-01..2026-10-06 (SPLIT_ADJUSTED; last available session, no partial current day) | G1/G2 | 2 (pagination) | bars with run id | key valid |
| 4 | Massive | SPY bars, same window, name "SPY (ETF proxy for the S&P 500)" | G2 | 2 | new levels with run id (no overlap with legacy 09-21..25) | key valid |
| 5 | SEC | FSDS 2026q2 ZIP download (manual) | G3 | 1 | local ZIP + SHA | human-approved download |
| 6 | SEC | `sec-filings` per chosen CIK | G3 | 2 | filings rows | UA valid |
| 7 | SEC | `sec-filings-history` only if the accession is older than `filings.recent` | G3 | 4 (1+N, N<=3) | history rows | optional |
| 8 | Finnhub | older-window discovery (stored ticker) | G5 | 1 | event or empty | conditional |
| 9 | Massive | snapshot at that event date | G5 | 1 | fact | conditional |
| 10 | Massive | bars (about 45 sessions) | G5 | 2 | bars | conditional |
| 11 | Massive | SPY bars, same window | G5 | 2 | levels | conditional |

Maximum total: G1+G2 = 6; G3 = 7; G5 = 6; **grand maximum 19 <= 30; expected about 10**.
Anything above a per-row maximum aborts (section 10).

## 10. Harness (design only; implementation requires separate approval)

Location: outside `src/` (for example `apps/api/scripts/` or a repo-level tools directory), not
shipped, no new public API.
- **Does:** builds `MassiveClient`, `FinnhubClient`, `SecClient` itself with a counting transport;
  calls existing services only (`IngestionService.ingest_reference / ingest_daily_bars /
  ingest_benchmark_daily_bars`, `EarningsIngestionService.ingest_earnings`, the listing-history,
  SEC filing and `FinancialFactIngestionService.ingest_fsds_package` services), then
  `save_research_record`, `session.commit()`, `verify_stored_record`, and read-only queries. On
  failure it commits only a durable failed run (via the public `FAILED_RUN_RECORDED` marker,
  mirroring the CLI's private helper).
- **Does not:** parse provider responses, duplicate business logic, write canonical rows directly,
  bypass validation, construct events, set `observed_at`, or create filing/event links.
- Forced wiring only: no earnings CLI and no composition root for Finnhub, so the harness builds
  `FinnhubConfig` from settings; no save CLI.

**Request counting.** All three clients accept `transport: httpx.AsyncBaseTransport` (keyword-only;
PROVEN). A shared counting transport wraps the real transport, increments before forwarding
(retries and pagination count), and raises `BudgetExceeded` (a plain `Exception`, never an httpx
error, so clients neither retry nor wrap it) **before** sending the request that would exceed a
per-provider or global cap. Per-provider counts are written to a JSONL ledger outside `src/`
containing only provider, method, host+path, query parameter names, status and counter; never
headers or bodies. Harness configs also set low `max_retries`/`max_pages`. Expect a durable failed
run row if the cap fires. No production change is required.

## 11. Idempotency and conflict proof (corrected)

No live idempotency claim rests on a mechanism that does not exist.
- **Already proven live earlier (retained evidence):** AAPL daily bars, SPY benchmark bars and VFS
  Finnhub earnings each have a second run with `written=0`, `skipped=received`.
- **Proven by existing tests (cite, do not re-run live):** daily, benchmark, earnings, reference,
  SEC filings/history, splits, listing history, FSDS, record save (names in the audit notes).
- **11.2B live proof:** run the same bounded service call a second time only where its request
  budget allows (items 2-4, one request each). Alternatively, a harness technique, not a
  production path: feed the retained bytes through the real client with an `httpx.MockTransport`
  (classes C and B) so the real parser and persistence run offline; caveats: sidecars omit HTTP
  status/headers (assume 200), multi-page Massive order comes from the manifest, and the replay
  run is labelled a replay, never a retrieval. FSDS replays natively.
- **Conflict tests:** local/integration mechanisms only (throwaway DB), never the golden rows.

## 12. Expected-outcome table

| Case | Source data | Descriptive | PIT | Accounting | Benchmark | Reproducibility | Blocking uncertainty |
|---|---|---|---|---|---|---|---|
| G1 NKE 2026-10-01 AMC | snapshot fact on 10-01; Finnhub event; NKE bars 10-01..10-06 | listing RESOLVED via SOURCE_STATED_HISTORY (venue not established); EPS surprise computed from Finnhub actuals (descriptive); anchor 2026-10-02; horizon 1 AVAILABLE; horizons 5/20 `INSUFFICIENT_FORWARD_SESSIONS` | REFUSED `INELIGIBLE_NO_AVAILABILITY_EVIDENCE`; descriptive intact | NOT_REQUESTED | NOT_REQUESTED | COMPLETE expected (event and bars carry run ids) | dated-snapshot entitlement; whether 10-06 bar exists |
| G2 NKE + SPY | G1 + SPY bars | same, plus backend-supplied abnormal return for AVAILABLE horizons | same | NOT_REQUESTED | PINNED if a stock return is AVAILABLE | **COMPLETE TARGET — NOT YET PROVEN**; MATCH on verify if COMPLETE | composed ingest-to-COMPLETE path untested; SPY new levels must not overlap legacy dates |
| G3 filing (conditional) | FSDS ZIP + `sec-filings` | n/a (no event link) | n/a | filing-level EPS statuses as returned; no PIT claim; `PERIOD_UNVERIFIED` if linked | n/a | n/a | ZIP absence; accession choice; comparative may refuse (52/53-week) |
| G5 +20 (conditional) | older event + bars | horizon 20 AVAILABLE if 20 later stored sessions exist | REFUSED (same) | NOT_REQUESTED | optional | COMPLETE target | Finnhub and Massive history entitlement |

Independent expectation: any EVIDENCE_ELIGIBLE PIT result in any case is P0.

## 13. Validation checks (unchanged intent; per case)

Raw artifact SHA-256 recompute, byte length, content-addressed path, run association, no secret in
sidecars. Lineage: UI figure -> observation field -> canonical row -> run -> manifest -> raw file.
Event identity fields (company, CIK, security, listing, date, timing, source identity, fingerprint,
`observed_at`/basis NULL, never `ingested_at`). Stored-session alignment only (no calendar
arithmetic). Independent exact-`Decimal` recomputation of stock return, benchmark return and
abnormal return for the available horizons (P0 on mismatch). Save, load, re-hash, repeat save
(`created=False`), fingerprints, level and gaps; verify immediately (COMPLETE -> MATCH). Local
drift exercise on a throwaway DB (`MANIFEST_CHANGED`, `RESULT_CHANGED`). For G3: package SHA,
submission, accession binding, `accepted` verbatim text, EPS tag/unit `USD`/qtrs/period_end/empty
dimensions, comparative by the same-accession 12-month-end rule; no PIT claim.

## 14. Real-data browser validation, performance, bounds

Browser (real API, production build): Overview, Earnings, event detail, Research workspace
(descriptive present, PIT refusal in its own frame, benchmark), Filing detail if G3, Saved Research,
`?verify=1`. Fixture E2E stays the regression suite. Measure ingestion duration, request counts,
observation SQL count and latency, save/verify latency, frontend request counts against the pinned
budgets; no thresholds yet.
Data bounds: events <= 2 per case; stock and benchmark window <= 10 calendar days (G5 <= 60); one
CIK of history, <= 3 history files; <= 2 FSDS packages (expected 1); saved records <= 4 in total
(G1, G2, optionally G3/G5).

## 15. Failure classes, STOP conditions, secret hygiene

P0: silent wrong value; PIT eligibility from unsupported evidence; wrong company/listing identity
(including a wrong historical resolution); provenance pointing at the wrong artifact; record
integrity failure; return-audit mismatch; any secret in any artifact. P1: a valid case cannot
traverse the pipeline; verification changes with unchanged evidence; frontend misrepresents backend
state. P2/P3: performance, copy, enrichment, diagnostics (unrecorded partial-run reasons, legacy
SPY run ids).
STOP the affected case on: missing credential; entitlement failure (record as provider evidence; no
scraping or unofficial sources); provider schema drift; raw artifact conflict; canonical conflict;
wrong listing resolution; unexpected PIT eligibility; financial mismatch; provenance mismatch;
record-integrity failure; verification mismatch; P0/P1 frontend mismatch; any cap reached. No
auto-repair: report evidence, expected, actual, blast radius, recommended repair slice. No manual
edits of canonical rows, no inserted `observed_at`, no hand-made links.
Secrets: no key, token, Authorization header, SEC contact, DSN password or secret query parameter
in any report, ledger, fixture, screenshot or raw sidecar; a redaction scan over reports,
`data/raw` sidecars/manifests, run `parameters` and browser artifacts is part of 11.2B.

## 16. 11.2B execution plan (not to be run without approval)

A preflight (read-only: DB backup, config presence, `listing-eras NKE`, harness review);
B approved provider calls under the counting transport; C ingestion in order: snapshot ->
earnings -> NKE bars -> SPY bars (-> G3/G5 gated steps); D canonical-row inspection and lineage;
E observations; F manifest inspection; G save; H verify; I real-data browser checks; J lineage
report. First action after approval: the single Massive dated-snapshot request (call 1); its
entitlement outcome gates everything else.

## 17. Roadmap

11.2B controlled real-data execution -> 11.2C repair only if P0/P1 -> 11.2D release-candidate
regression/freeze -> later, separately: authentication, ownership and user-facing Save. Candidate
small slice if the harness proves awkward: earnings CLI, save CLI, recorded partial-run reasons,
benchmark run-id backfill (not assumed).

## 18. Unresolved

Dated-snapshot entitlement; older Finnhub and Massive history depth; FSDS ZIP acquisition and
accession choice; a never-tested composed COMPLETE path; harness approval and location; the
last-available NKE session on 2026-10-06/07.
