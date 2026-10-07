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
| G1 NKE identity + descriptive, Configuration A: stock-only (event 2026-10-01 AMC, reported) | **DEFERRED after 11.2B (SOURCE_SEMANTICS_INSUFFICIENT; see section 19). Was: APPROVED CANDIDATE** (conditional on Massive dated-snapshot entitlement, UNKNOWN) | Proven failure mode; fixed only by a sourced fact on the event date; real reported event already seen in retained Finnhub bytes |
| G2 Configuration B: the SAME event + explicit SPY ETF-proxy benchmark | **DEFERRED with G1 (section 19). Was: APPROVED CANDIDATE; COMPLETE TARGET — NOT YET PROVEN** | All components can carry run ids on the planned path (event, stock bars, new SPY bars); composed path untested; must have an AVAILABLE return to avoid vacuous COMPLETE |
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

## 19. 11.2B capability probe results (2026-10-07) — OBSERVED vs NOT YET VALIDATED

Harness: `scripts/validation/` (untracked, outside production source; artifacts git-ignored).
Isolation: a dedicated database `quantforge_validation` created inside the existing Postgres
container and migrated with the normal Alembic chain to `0013`; the dev database was not touched.
Reason: it lets reference ingestion recreate the real "first observed today" state and keeps run-id
provenance free of the legacy NULL-run SPY rows. Observation: the `QF_DATABASE_URL` in `.env`
does not authenticate against the running container, while the application default URL does
(configuration drift; not investigated further).
External requests: **10 of the hard cap 12** — Massive 8, Finnhub 1, SEC 1. Two of the Massive
requests were harness-bug re-fetches while diagnosing a failure (the client raised after sending);
all retries and attempts count. Provider call times were 0.6-1.2 s each.

### OBSERVED
| Question | Result |
|---|---|
| Massive dated snapshot, NKE 2026-10-01 (1 request) | HTTP 200, 1 record. Payload keys `count, request_id, results, status`; record keys `active, cik, composite_figi, currency_name, last_updated_utc, locale, market, name, primary_exchange, share_class_figi, ticker, type`. `active=true`, `primary_exchange=XNYS`, CIK and share-class FIGI match the stored NKE. Raw stored with SHA. |
| Does the response state the requested date? | **No.** The payload carries no as-of field. `last_updated_utc` is 2026-10-02T06:11:31Z, i.e. AFTER the requested date. |
| What would the mapper do? | `to_canonical_ticker_snapshot(as_of_date=on_date)`: it stamps the QUERY date 2026-10-01 as the fact date (PROVEN by running the mapper; nothing was ingested). |
| `list_date` | Absent from the dated-snapshot (list) endpoint. Present only in the single-ticker detail endpoint (retained reference payloads: NKE 1980-12-02, AAPL 1980-12-12, VFS 2023-08-15). Repository docs retained do not define it; any "first publicly listed" meaning is UNVERIFIED. |
| **NKE classification** | **C. SOURCE_SEMANTICS_INSUFFICIENT.** The payload does not establish validity on 2026-10-01; the date semantics rest on an unretained provider contract, and no negative control (a date the ticker must be absent) has been run. Under the 11.2B stop rule the fact was NOT ingested and the mapper was NOT changed. NKE 2026-10-01 is not the golden event until this is resolved. |
| Finnhub (1 request, AAPL 2026-07-01..2026-08-15, not ingested) | HTTP 200, `earningsCalendar` present, **0 events**. Older-history depth is NOT established: an empty answer is consistent with a history limit but does not prove one. `observed_at` behavior could not be exercised (no event returned); mapper still hard-codes None. |
| Massive reference NKE (1 request, ingested into the validation DB) | succeeded 1/1; listing `valid_from` = 2026-10-07 (first observed), as designed. |
| NKE stock bars 2026-09-01..2026-10-06 (ingest) | **FAILED run**, `ProviderPayloadError`: 2026-10-01 `vwap=34.8298` is outside the session range [35.02, 36.235]. One malformed record fails the entire run; the failed run kept NO raw artifact (`raw_artifact_path` NULL) and the client attaches no captures, so the offending bytes were not retained. A diagnostic fetch (inspection only, not ingested; SHA be43e197…, 582 B) shows four sessions 10-01, 10-02, 10-05, 10-06 and `vw` below `l` on 10-01. Hypothesis (NOT verified): on an after-market-close event day the vwap includes extended-hours trades. 2026-10-07 is not served yet. |
| NKE bars from the AMC anchor, 2026-10-02..2026-10-06 | succeeded 3/3 (10-02 close 33.87, 10-05 33.96, 10-06 34.61); **3 of 3 bars carry the writing `ingestion_run_id`**. Anchor +1 and +2 exist; +5 does not exist yet; **+20 NOT VALIDATED**. The window starts at the anchor on purpose (research needs the anchor onward); the 10-01 bar defect remains open. |
| SPY (ETF proxy) 2026-09-01..2026-10-06 | succeeded 25/25 bars, **25 of 25 carry the writing run id** (one run). Labelled "SPY (ETF proxy for the S&P 500)". Bar depth: served back at least to 2026-09-01. |
| Composed ingestion -> observation -> manifest -> COMPLETE | **NOT PERFORMED.** No event with a defensible listing exists. Provenance preconditions for COMPLETE are now observed for new stock and benchmark bars; the composed result is still NOT YET VALIDATED. |
| Saved record / verification | NOT PERFORMED. |
| PIT | Not exercised (no event). Expectation unchanged: REFUSED `INELIGIBLE_NO_AVAILABILITY_EVIDENCE`. |

### FSDS (1 SEC request)
Package chosen first: **2026q2**, latest quarter the repository records. Official URL path
`/files/dera/data/financial-statement-data-sets/2026q2.zip`; HTTP 200; Last-Modified Wed 19 Aug
2026 01:09:50 GMT; 60,419,016 bytes; SHA-256
`d7c815395cd420cfbe09b29dbae73a45406c5d0652d5ae1859ae11ea2e22df77`; stored locally under the
git-ignored artifacts directory. The production `FsdsPackage` reader opens it with no network.
Contents: 7,714 submissions (10-Q 5,321; 10-K 570; 10-Q/A 37; 10-K/A 72), filed 2026-04-01 to
2026-06-30; 3,608,711 NUM rows; 5,467 accessions with an unsegmented, non-coreg `USD`
EarningsPerShareDiluted fact; 4,813 10-Q/10-K accessions with exactly one current fact and 4,732 of
them with exactly one comparative exactly 12 month-ends earlier.
Both stored companies appear: **NKE 0000320187-26-000037** (10-Q, FY2026 Q3, period 2026-02-28,
filed 2026-04-01, `accepted` text `2026-04-01 16:17:00.0`) with diluted EPS 0.3500 vs comparative
0.5400 (qtrs 1; YTD qtrs 3 rows also present); **AAPL 0000320193-26-000013** (10-Q, FY2026 Q2,
period 2026-03-31, filed 2026-05-01) 2.0100 vs 1.6500. AAPL is therefore a PROVEN accounting
candidate for this package (upgraded from UNPROVEN); neither accession is the fiscal period of the
NKE 2026-10-01 event, so neither may be linked to it.
Candidates (at most five; the last three are a seeded random sample, not curated):
1. NKE 0000320187-26-000037, 10-Q, current + comparative (profit decreased). Recommended G3: the
   company already exists in the validation database.
2. AAPL 0000320193-26-000013, 10-Q, current + comparative (profit increased; 52/53-week rounded to
   month-end 2026-03-31, comparative matched).
3. THERMON GROUP 0001489096-26-000067, 10-K, FY period 2026-03-31, 1.36 vs 1.57 (annual path).
4. ORGANOGENESIS 0001661181-26-000008, 10-Q, -0.44 vs -0.17 (loss path).
5. NEXTPOWER 0001852131-26-000017, 10-K, 3.84 vs 3.47.
SEC filings requests for 11.2C: `sec-filings 0000320187` (1 request) then offline `sec-fsds`.

### Findings (not repaired)
- **P1 candidate (semantic):** the listing-snapshot mapper dates a fact by the query date although
  the payload states no date; acceptable only if the date parameter's provider semantics are
  retained and a negative control passes. Repair/decision slice needed before any use.
- **P1 candidate (pipeline):** an AMC event-day bar can fail provider-record validation and fail the
  whole run (observed once); workaround is a window beginning at the anchor, which hides the defect.
- **P2:** malformed-record failures retain no raw artifact (evidence lost); partial/failed runs do
  not keep the source bytes that caused them.
- **P3:** `.env` database URL vs running container credentials drift; legacy SPY rows still lack run ids.
- Entitlement: Massive reference, daily bars and the dated-snapshot endpoint answered 200; no 401/403
  seen. Finnhub older-history depth and Massive depth beyond 2026-09-01 remain UNKNOWN.
- Secret hygiene: the configured Massive/Finnhub keys, the SEC User-Agent and the DB password were
  searched for in all harness files, the design doc and the validation DB run rows: no hits (one
  alphabetic ten-letter default-password match in source files is a false positive: it equals the
  project word in import lines). The request ledger holds only provider, host, path, query-name,
  status and timing.

### Proposed 11.2C
Do not ingest any listing fact until the snapshot `date` semantics are established from a retained
official statement plus one negative control. Candidate corpus: (a) NKE 2026-10-01 only if that
passes (then re-run bars from the anchor, with the event-day vwap defect recorded); (b) otherwise a
time-gated natural event for an already-referenced company (NKE estimate event 2026-12-16 resolves
on first-observed evidence once reported); (c) G3 filing-level accounting on NKE
0000320187-26-000037 (and optionally AAPL). First 11.2C action: obtain the official `date`
parameter semantics (documentation read, no API call), then at most one Massive negative-control
request.

## 20. 11.2C semantic adjudication (2026-10-07)

Status of earlier steps: **11.2B capability probe: COMPLETE. Golden-corpus authorization: NOT READY**,
because (A) historical snapshot effective-date semantics were unresolved, (B) event-day aggregate
validation semantics were unresolved, (C) no defensible event/listing chain was assembled, and
(D) the composed COMPLETE path therefore remains unproven.
11.2B request accounting (pinned): Massive 8, Finnhub 1, SEC 1 = **10 of 12**; two of the Massive
requests were repeated fetches caused by harness defects (the client raised after sending). 11.2C
added **one** Massive negative-control request (cap 1), so the harness ledger totals 11.
FSDS artifact (preserved, do not re-download unless SHA verification fails; verify SHA before use):
package `2026q2`, source `https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q2.zip`,
retrieved_at 2026-10-07T05:54:27Z (harness ledger request 10; no ingestion run exists, it is a
local validation artifact), 60,419,016 bytes, SHA-256
`d7c815395cd420cfbe09b29dbae73a45406c5d0652d5ae1859ae11ea2e22df77` (re-verified in 11.2C).

### 20.1 OFFICIAL EVIDENCE (Massive documentation, read 2026-10-07; no API call)
Dated snapshot endpoint: `GET /v3/reference/tickers` ("All Tickers"), as used by QuantForge with
`ticker`, `date`, `market=stocks`, `active`, `limit`.
- `date`: "Specify a point in time to retrieve tickers available on that date. Defaults to the most
  recent available date."
- `active`: "Specify if the tickers returned should be actively traded on the queried date."
- Response `active`: "Whether or not the asset is actively traded. False means the asset has been
  delisted."; `last_updated_utc`: "The information is accurate up to this time."; `delisted_utc`:
  "The last date that the asset was traded." Plan history: "Records date back to September 10,
  2003" (the docs list 2 years for the Basic plan, all history for paid plans).
- The documented sample response's `last_updated_utc` equals the date encoded in its own `next_url`
  cursor (2021-04-25). No response field carries an as-of date.
- Ticker Overview (`/v3/reference/tickers/{ticker}`): "active as-of a given date"; `list_date`:
  "The date that the symbol was first publicly listed in the format YYYY-MM-DD." Delisted tickers
  are directed to the All Tickers endpoint with `active=false`.
Aggregates endpoint: `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`.
- "...in Eastern Time (ET). Aggregates are constructed exclusively from qualifying trades that meet
  specific conditions... covering pre-market, regular market, and after-hours sessions."
- Fields: `o,h,l,c` "open/highest/lowest/close price for the symbol in the given time period",
  `v` "The trading volume of the symbol in the given time period", `vw` "The volume weighted
  average price." (no population stated), `adjusted` "Whether or not the results are adjusted for
  splits."
- Knowledge base, "How does Massive create aggregate bars?": each trade's sale conditions "decide
  whether it may update the open, high, low, close or volume, so a bar is a sum of eligible trades
  rather than of all trades"; one trade "can be allowed to do some of those and not the others";
  day bars are "continuously recalculated through the session, so it absorbs even very late
  trades"; bars are corrected "for about 15 minutes on the stream and again at the end of the day".
- Knowledge base, extended hours: "most extended-hours trades do not update a bar"; "Volume is
  often still updated even when price is not, which is why an extended-hours bar can have volume
  that its own high and low do not reflect."; "A bar whose volume looks inconsistent with its price
  range is a condition that updates volume but not price."
Official text does NOT say: what population `vw` is computed over; whether the daily bar's `vw`
uses price-updating trades only; that low <= vw <= high; or whether `adjusted` transforms `vw` and
OHLC identically (for the NKE window no split occurred, so adjustment is not a factor here).

### 20.2 OBSERVED EVIDENCE (11.2B/11.2C; not documentation)
- NKE dated snapshot for 2026-10-01: HTTP 200, one record, no as-of date in the payload,
  `last_updated_utc` 2026-10-02T06:11:31Z.
- Negative control (11.2C, 1 request, not ingested): VFS dated snapshot for 2023-06-01, a date
  before the source-stated `list_date` 2023-08-15 retained from a real detail payload: HTTP 200,
  **0 results**. A date parameter that was ignored would have returned the current VFS record (as
  the NKE query returned its record), so the parameter is evidently applied. Caveat (not
  resolvable here): the date is outside the 2-year window documented for the Basic plan, so an
  empty answer could also come from plan depth; the control corroborates and cannot replace the
  documentation.
- NKE 2026-10-01 aggregate (adjusted=true; inspected raw bytes, SHA be43e197…, 582 B, retained in
  the git-ignored diagnostic folder): o 35.45, h 36.235, l 35.02, c 35.15, **vw 34.8298**, v
  112,413,524.118262, n 560,588; next session 2026-10-02 opened 32.553. vw is 0.19 below the day's
  low. The event was after market close.

### 20.3 VERDICTS
**A. Snapshot `date`** — documented meaning: **A (tickers available / actively traded on that
date)** for the identity of the ticker and its `active` flag; the docs do not state that the other
attributes (name, CIK, FIGIs, `primary_exchange`) are as of that date. The absence of an as-of date
inside the response does not invalidate the request date as provenance: the provider contract
defines the request parameter as the point in time (request-scoped semantics), so no duplication in
the response is required. **Mapper verdict: B — CORRECT BUT PROVENANCE-INCOMPLETE.** The mapper
dates the fact by the query date (consistent with the contract) and the raw sidecar retains the
`date` parameter, but (i) QuantForge retains no copy of the contract wording that justifies the
mapping (this section is the first), and (ii) the mapper also stamps `primary_mic` for that date
although the docs do not make venue as-of-date; that field should not be relied on as dated venue
evidence. Not repaired.
**list_date** — documented: "first publicly listed", a Ticker Overview field; not defined as
ticker-specific vs security-specific, not defined across symbol changes or relistings, and returned
only by the detail endpoint. Classification: **AMBIGUOUS** as identity-era evidence (it could at best
corroborate "existed by" a date; it cannot give a ticker interval, a venue, or continuity). Not wired.
**Historical-listing implication:** the dated snapshot, used per event date, is defensible
single-date evidence under the documented contract; `list_date`, `TICKER_STARTED` (experimental
events endpoint, date semantics unverified) and sourced facts remain the only supported sources. No
heuristic, backward extension from first observation, gap filling or current-ticker inference is
allowed.
**NKE 2026-10-01 listing verdict: DEFERRED** (not rejected). The snapshot semantics are now
defensible, but the event cannot be approved for golden validation until the production
provenance gap (retaining the contract wording and the date parameter against the fact) and the
event-day bar question below are decided, and no listing fact was ingested in this slice.

**B. Aggregate VWAP invariant — classification D (UNKNOWN), with a documented counter-mechanism.**
Official semantics do not establish `low <= vw <= high`, and the documented eligibility asymmetry
(a trade may update volume and not price) is a mechanism by which an aggregate's volume-derived
statistic can fall outside its price range. Whether `vw` is built from all volume-updating trades
is not stated. The after-hours explanation of the NKE record (event after the close; heavy
extended-hours volume that does not update the day's low) is **consistent with** the documentation
but is **not established** by it. One violating record does not justify removing the invariant
(rule: no relaxation by example); but the invariant is not justified by the documentation either:
the code comment/test calling it "impossible for a real session" is a design assumption, not a
provider guarantee. This is recorded as a design correction to decide, not silently accepted and
not silently relaxed.

**Failed-raw trace (PROVEN from code):** request (`_get_json`) -> raw capture appended to a LOCAL
list in `get_daily_bars` -> results decoded to DTOs -> after the paging loop, `to_canonical_daily_bar`
runs in a list comprehension; the canonical dataclass raises on `vwap` outside [low, high] -> the
exception leaves the client with no captures attached (only basis-mismatch paths attach them) ->
the service has stored nothing yet (`_record(run, result.captures)` is only reached after a
successful return; the only `except` that persists bytes is for `ProviderBasisMismatchError`) -> the
run recorder rolls the SAVEPOINT back and writes a `failed` row with error type/message but no
artifact path. **Classification: PROVENANCE GAP** (diagnostics gap that also loses the bytes needed to
reproduce a provider-semantic failure; not an invisible loss of canonical data, since nothing wrong
was stored). **Whole-run behavior:** one malformed bar aborts the entire requested range
(first raise in the comprehension; no per-record rejection for bars, unlike dated snapshots and
reference, which count rejected records). No test or doc pins this as intended: **UNCLEAR**, leaning
accidental relative to the sibling paths.
**Anchor-window issue:** the research anchor for an after-market event is the first stored session
strictly after the event date, and returns are measured from the anchor close onward, so the
event-day bar is not an input to the study. A window starting at the anchor can therefore ingest
cleanly and look healthy while the event-day record is never examined. It is not a bug that
the normal contract requires that bar; it is a coverage blind spot. For a before-market event the
event-day bar IS the anchor, so the same provider behavior would block the anchor itself.
**Provenance (preserved positive result):** newly ingested NKE stock bars (3/3) and SPY bars
(25/25) carry the writing `ingestion_run_id`. This proves the individual new-bar path, **not** the
composed COMPLETE path (no event/listing chain was assembled).

### 20.4 Accounting vertical (offline; production FSDS reader and EPS selectors; no SEC request)
SHA of the preserved ZIP re-verified. Accession **0000320187-26-000037**: CIK 0000320187, form
10-Q, period end 2026-02-28, FY2026 Q3, fiscal year end 0531, filed 2026-04-01, source-stated
`accepted` text `2026-04-01 16:17:00.0` (verbatim; not availability). Production
`select_comparative_eps(QUARTER, DILUTED)`: `EarningsPerShareDiluted`, `us-gaap/2025`, unit `USD`,
qtrs 1, empty segments and coreg; current 0.3500 (2026-02-28); comparative 0.5400 (2025-02-28, 12
month-ends earlier); status AVAILABLE; production `compute_eps_change`: -0.1900,
POSITIVE_DECREASED, ratio -0.35185185185185185185185185185185185185185185185185. Not linked to the
NKE 2026-10-01 event (different fiscal period); same company is not event evidence.
Verdict: **APPROVED ACCOUNTING GOLDEN CANDIDATE**, independent of the market event. Requirements for
11.2D: one bounded `sec-filings 0000320187` request to create the `SecFiling` row, then offline
`sec-fsds` with this accession.

### 20.5 Corpus architecture and consequences
Two independent axes: (1) MARKET EVENT golden case (event, listing, bars, benchmark, PIT,
reproducibility) and (2) ACCOUNTING golden case (filing, FSDS, current/comparative/change). They may
share a company for convenience, which establishes no link. The December NKE event is NOT adopted
automatically; it qualifies only as a fallback if it is a real provider event, covered by
defensible listing evidence, able to accumulate sessions, and does not hide an unresolved
production semantic. No request for it was made.

### 20.6 Defect classification
| Issue | Severity | Rationale |
|---|---|---|
| Historical snapshot fact dating | **P3** (provenance-incomplete; mapping defensible) | Contract supports the fact date; missing retained contract text; `primary_mic` not documented as dated |
| Daily VWAP invariant | **P1 candidate (design correction)** | Not guaranteed by documentation; hard failure can block the anchor bar of before-market events and aborts the whole run; needs a decision rather than a patch-by-example |
| Failed raw retention | **P2** | Bytes needed to reproduce a provider failure are lost |
| Whole-run rejection | **NEEDS MORE EVIDENCE** (leaning P2) | Fail-closed may be intended; sibling paths reject per record; no pinning test |

### 20.7 Next-phase branch
**BRANCH B — NARROW PRODUCTION REPAIR REQUIRED**: 11.2D repair design (bar validation policy for
`vw`, retain rejected-response bytes on bar failures, whole-run vs per-record decision, retain the
snapshot contract text and demote `primary_mic` from dated evidence), then 11.2E implementation,
then golden validation. The accounting vertical does not depend on the repair and may proceed in
parallel. Remaining unknowns: VWAP population; plan-depth effect on the negative control; whether
the Basic-plan limits apply to this key.

## 21. 11.2D repair design (cross-reference)
Design only; see `docs/market-data-repair-design.md`. Summary: remove the unsupported `low <= vwap <=
high` rule (P1); retain raw bytes of failed bar responses as `-rejected` evidence via the existing
run columns (P2); keep whole-range fail-closed with all evidence retained (Option C) because
per-record partial storage would manufacture silent interior-gap session shifts — a new P1 finding
(research counts stored bars as sessions and never compares to a calendar), to be designed
separately as 11.2F; stop persisting snapshot `primary_mic` (P3, dead data, 0 rows) and pin the
snapshot date contract with named constants. No migration. Golden validation stays blocked until
11.2E (and the 11.2F decision) are done; the NKE accounting candidate remains approved but not
ingested.


## 22. 11.2E repair and bounded real NKE validation (2026-10-07)

See `docs/market-data-repair-design.md` section 13. The repairs landed (uncommitted): session
correctness from the exchange calendar, vwap containment removed, raw evidence kept on failed bar
responses, snapshot `primary_mic` demoted. Offline gates: backend 3360 tests (2372 unit + 988
integration), 95.79%; frontend 1400 unchanged; migration head 0013.

### OBSERVED (post-repair, validation DB rebuilt by normal migrations; request ledger 5 of cap 8)
| Step | Result |
|---|---|
| Massive reference NKE (1) | succeeded; listing `valid_from` 2026-10-07 (first observed) |
| Massive dated snapshot 2026-10-01 (1) | `TICKER_OBSERVED NKE 2026-10-01`, `mic` NULL, run id present |
| Finnhub NKE 2026-09-25..10-02 (1) | the 2026-10-01 AFTER_MARKET event stored (reported 0.48, estimated 0.4364), `observed_at` NULL, run id present |
| Massive NKE bars 10-01..10-06 (1) | **4/4 stored including 2026-10-01 (vwap 34.8298 < low 35.02)**; every bar has its run id |
| Massive SPY ETF proxy 10-01..10-06 (1) | 4/4 stored, all run ids |
| Listing | RESOLVED, SOURCE_STATED_HISTORY, resolver `exchange_mic` None (venue not established), calendar venue XNYS (the listing's exchange) |
| Sessions | anchor 2026-10-02 (AFTER_MARKET); +1 2026-10-05 and +2 2026-10-06 AVAILABLE; +5 = 2026-10-09 proven by the calendar, no bar yet (`INSUFFICIENT_FORWARD_SESSIONS`); +20 NOT VALIDATED |
| Returns (exact Decimal, independently recomputed from stored closes, match) | stock +1 0.002657218777679362267493357, +2 0.021848243283141423088278713; SPY +1 0.006743412504547580687074476, +2 0.012278467855101086222129827; abnormal +1 -0.004086193726868218419581119, +2 0.009569775428040336866148886 |
| PIT | REFUSED `INELIGIBLE_NO_AVAILABILITY_EVIDENCE`, descriptive intact (as predicted; no eligibility anywhere) |
| Manifest (stock-only, and with explicit SPY ETF proxy) | event present; listing RESOLVED; stock PINNED; benchmark NOT_REQUESTED / PINNED; filing and accounting NOT_REQUESTED; **COMPLETE, no gaps** |
| Save and verify | one record saved through `save_research_record` + commit; `verify_stored_record` = **MATCH** (save 0.021 s, verify 0.009 s; assembly 0.29 s cold / 0.007 s warm) |

**COMPOSED COMPLETE PROVEN**: ingestion -> research -> reproducibility, with every run id written by
the ingesting service and no SQL repair. Secret scan clean (38 files, no key, no User-Agent).
NOT YET VALIDATED: +5/+20 on real data (sessions have not occurred), the accounting vertical
(NKE `0000320187-26-000037` approved, not ingested), real-data browser checks.
