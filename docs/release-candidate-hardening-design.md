# Release-candidate hardening: audit and design (Step 12A)

Status: audit and design only. No production change, no migration, no provider request.
Baseline: `e79a5c37b91b252e8b5e71e85ea3c1616b1c1c86` (Step 11.2 frozen, head `0014`, tree clean).
Evidence: code reads, an empirical API/UX pass against the validation database
(`quantforge_validation`, one NKE event, one FSDS filing, 164 filing identities, one saved record) and
the production build. Goldens (market, accounting, calendar alignment, VWAP, SEC identity, `accepted_at`,
FSDS selectors, reproducibility, verification, PIT refusal, evidence wording) are regression baselines.

Classification vocabulary: severity P0-P3, and scope **RC BLOCKER** / **RC REQUIRED** / **POST-RC**.

## 0. Headline

No P0 and no P1. The validated data/research core is sound. What stands between the product and a
release candidate is a small set of **P2 consistency and operability gaps** plus missing operator
documentation:

1. one semantic imprecision visible to users (proven target session, price absent, reported as "Too few stored sessions");
2. successful provider bytes can still be lost on a parse failure in reference and earnings ingestion;
3. partial/failed runs carry no durable reasons and the CLI does not tell an operator which run failed;
4. readiness neither checks the schema revision nor tolerates the (currently unused) Redis being down;
5. no DB connect/statement timeouts, and production can start on default credentials;
6. two read endpoints accept repeated scalar parameters (last value wins);
7. there is no runbook, no backup/restore procedure and a stale README.

Everything else is P3 or post-RC.

## 1. Product inventory (what exists)

| Route | Purpose | Backend reads (exact budget) | States seen empirically |
|---|---|---|---|
| `/` Overview | where to start; latest events, filings, saved; system status | health, ready, `earnings-events?limit=5`, `filings?limit=5`, `research/records?limit=5` (5) | ok, partial-empty, unavailable per panel |
| `/earnings` | event list with ticker/date filters, paging | health, `earnings-events?limit=50` (2) | ok, empty, filter-invalid, past-end, unavailable |
| `/earnings/[id]` | stored event, availability, market preview | health, event, observation (3) | ok, not found, unavailable |
| `/research` | event finder | health, events (2) | ok, empty, unavailable |
| `/research/events/[id]` | descriptive study, PIT verdict, optional benchmark/filing, refusal ledger | health, event, benchmarks, observation (4) | ok, refusals, config-invalid, not found, unavailable |
| `/filings` | filings with FSDS data | health, `filings?limit=50` (2) | ok, empty, filter-invalid, unavailable |
| `/filings/[accession]` | exact EPS current/comparative/change | health, filing EPS (2) | ok, refusal statuses, param-invalid, unavailable |
| `/research/saved` | saved baselines, current-event enrichment | health, records, one event lookup per distinct event on the page (page size 20) | ok, empty, unavailable |
| `/research/saved/[id]` | stored baseline; explicit verify | health, record (2); with `?verify=1` + verify (3) | ok, not found, mismatch states, unavailable |

Known limitations: read-only product (no save UI, no auth, no accounts); no filing is ever linked to an
event automatically; only FSDS-backed filings are listed; `+N` horizons beyond stored bars show a refusal.

## 2. Backend surface

All public routes are `GET`. Verified with the live app: valid UUID/accession/CIK patterns, `limit` 1-200,
`offset` 0-1,000,000, `ticker` <= 32 chars, horizons 1-252 and at most 10, unknown query parameters rejected
(422 `extra_forbidden`) on every data endpoint except the earnings detail (which ignores them), 404 for unknown event/record, domain refusals as HTTP 200, a database
outage as a generic 503 `{"detail":"Database unavailable."}` on every data route (no SQL, DSN, path or class
text), a generic 500 only for a stored-record integrity failure, 405 for non-GET.

| Endpoint | Pagination / order (deterministic) | Repeated scalar params | 503 documented in OpenAPI |
|---|---|---|---|
| `GET /health`, `/api/v1/health` | - | n/a | n/a |
| `GET /api/v1/health/ready` | - | n/a | yes |
| `GET /earnings-events` | limit/offset; `event_date DESC, fiscal_year DESC, id` | **not rejected (last wins)** | no |
| `GET /earnings-events/{id}` | - | **not rejected** | no |
| `GET /benchmarks` | `name, id` | rejected 422 | yes |
| `GET /filings` | `filing_date DESC, accession DESC, cik DESC, id` | rejected | yes |
| `GET /filings/{acc}/eps` | - | rejected | yes |
| `GET /research/observations/{id}` | - | rejected | yes |
| `GET /research/records` | `created_at DESC, id DESC` | rejected | yes (+500) |
| `GET /research/records/{id}` and `/verify` | - | n/a | yes (+500, +409 on verify) |

Inconsistencies found: (a) the two earnings endpoints lack the repeated-parameter guard
(`?ticker=NKE&ticker=AAPL` silently queries AAPL) and the detail endpoint also ignores unknown parameters, (b) they also omit the 503 from their OpenAPI
`responses` although they return it, (c) `horizons` has no schema bounds (validated in code, still a clean 422).

## 3. Ingestion inventory

| Operation | CLI | Provider | Bounded | Raw capture | Run record | Idempotent retry | Conflict | Failure behaviour |
|---|---|---|---|---|---|---|---|---|
| reference | `ingestion reference` | Massive | per ticker list | at end of batch (see 4.3) | yes | resolver matches existing | `ReferenceResolutionError` | per-ticker failure counted, run `partial` |
| earnings | **none** (service/harness only) | Finnhub | 1 request | before canonical | yes | digest dedup (skip identical) | none by design: a changed fact is a new revision row | provider error propagates; per-event failures counted, `partial` |
| daily bars | `ingestion daily` | Massive | range, paged cap | accepted, or `-rejected` on failure | yes | `(listing,session,source,basis)` | `ProviderDataConflict` | whole range fails, raw kept as rejected |
| benchmark bars | `ingestion benchmark` | Massive | same | same | yes | same | `BenchmarkBarConflictError` | same |
| SEC filings / history | `sec-filings`, `sec-filings-history` | SEC | 1 + <= N history files | submissions bytes (rejected on failure) | yes | identity skip | `SecFilingConflictError` (identity only) | whole-response refusal on structure |
| SEC acceptance | `sec-filing-acceptance` | SEC | exactly 1 request | header bytes | yes | no request if established | hard conflict, no overwrite | stays NULL |
| FSDS | `sec-fsds` | none (local ZIP) | named accessions/CIKs | package copy + SHA | yes | package revision refused | `FinancialFactConflictError` | whole run fails |
| splits, listing facts | `stock-splits`, `listing-*` | Massive | per ticker | yes | yes | fact conflict | explicit errors | explicit errors |

Operator view: `ingestion runs` lists provider/dataset/status/counts only (no error, no raw path, no status filter).

## 4. Backlog triage

### 4.1 Missing-target semantics (audited first)

**Current.** `compute_forward_returns` uses `INSUFFICIENT_FORWARD_SESSIONS` for two materially different
evidence states: (i) the calendar proves fewer than N sessions after the anchor (`target_session = None`), and
(ii) the N-th session is proven but its price bar is absent (`target_session` set, `target_close = None`).
The reason text separates them; the status code and the frontend label ("Too few stored sessions") do not, and
the label is false for (ii): the session is known, only a price is missing. The benchmark leg already has the
right name: `BenchmarkReturnStatus.MISSING_TARGET_PRICE` (and `MISSING_ANCHOR_PRICE`).

**Distinct evidence states and where each lands today**

| State | Today | Proposed |
|---|---|---|
| no session/calendar evidence | `NO_ALIGNMENT` (alignment `NO_SESSION_DATA` with reason) | unchanged |
| unsupported venue/calendar | `NO_ALIGNMENT` (alignment reason) | unchanged |
| missing anchor price | `MISSING_ANCHOR_PRICE` | unchanged |
| calendar proves fewer than N sessions | `INSUFFICIENT_FORWARD_SESSIONS` | unchanged (keeps the name, now accurate) |
| target proven, price absent | `INSUFFICIENT_FORWARD_SESSIONS` | **`MISSING_TARGET_PRICE`** (mirrors the benchmark status) |
| benchmark anchor / target missing | `MISSING_ANCHOR_PRICE` / `MISSING_TARGET_PRICE` | unchanged |

**Future target (+5 = 2026-10-09 at validation time): no separate state.** In the stored-evidence model a
not-yet-occurred session and a historical session whose bar was never ingested are the same evidence: a proven
session with no stored price. Telling them apart needs a clock (`target >= today`), which would make a
deterministic result depend on wall time: the same inputs would produce a different result tomorrow, breaking
fingerprints and verification by the mere passage of time. Rule: **never introduce `TARGET_NOT_YET_OBSERVABLE`
in the domain.** If wanted later it is a presentation-only hint computed from a stored fact already in the
payload (for example the latest stored bar date), never a status.

**Frontend wording (neutral, no "closed" implication)**

| Status | Label | Meaning shown |
|---|---|---|
| `MISSING_TARGET_PRICE` | "Price not stored for target session" | the session is proven by the exchange calendar; no price observation is stored for it; the target is not moved |
| `INSUFFICIENT_FORWARD_SESSIONS` | "Beyond the calendar's proven horizon" | the calendar proves fewer than N sessions after the anchor |
| `MISSING_ANCHOR_PRICE` | "Missing anchor price" | unchanged |
| `NO_ALIGNMENT` | unchanged | unchanged |

**Contract cost (exact).**
- Backend production: `research/returns.py` (enum member, one `results.append`, docstrings), the methodology label
  `forward_returns` bumped to `close-to-close-proven-session-returns/2` in `reproducibility/methodology.py`
  (semantics changed, so identity must change). No change to `observation/models.py` (refusal codes derive from
  the status), `schemas/research_observation.py` (enum re-exported into OpenAPI), signals, abnormal or benchmark code.
- **Golden consequence, explicit.** The saved record `e880eaf9` stores a `+5` outcome with a proven target and no
  price. After the bump it verifies `MANIFEST_CHANGED` (the established 11.2E pattern, tested), not `MATCH`; stored
  baselines are never rewritten. The market golden values themselves (anchor, +1, +2, abnormal, PIT) do not change;
  a new golden record is saved after the change. This must be accepted explicitly in 12B.
- OpenAPI/Pydantic: one enum value; `npm run contract:export` regenerates `contract/openapi.json` and
  `src/types/generated/api.d.ts`.
- Strict frontend decoder: `lib/api/schema.ts` enum list (a missing value would fail decoding by design),
  `lib/research-wording.ts` (label and reason), `lib/state-map.ts` (`MISSING`). Research workspace and Saved
  Research both render through these maps; nothing else.
- Fixtures: `tests/contract/observation.json` and `records.json` carry the status; the entries whose
  `target_session` is set and `target_close` null change to the new value; fixture API (`e2e/fixture-api.mjs`) serves them.
- Tests: backend `test_research_returns`, `test_research_sessions_real_calendar`, `test_research_session_gaps`,
  `test_research_event_returns`, `test_research_event_study`, `test_research_observation`, `test_composed_market_chain`,
  signals/abnormal tests that name the status; frontend `research-chart`, `research-page`, `research-contract` tests.
- Real-smoke harness: unaffected (it asserts no overflow/axe, not statuses).
- Size: small-medium; risk medium-low because the change is a relabel plus a tested methodology bump.

### 4.2 Partial bar storage - **POST-RC**

Why it is not a quick fix: a partially accepted range needs a per-interval truth that the schema cannot
express. `ingestion_run` stores counts and `request_parameters` (start/end) but not which dates were provider-returned,
accepted, rejected or expected; `daily_price_bar.ingestion_run_id` says who wrote a bar, not what the run failed to write.

Required before it is safe: a coverage record per requested interval and source/basis holding requested range,
provider-returned dates, accepted dates, rejected dates with reasons, missing expected sessions (derived from the
exchange calendar, not from stored bars), raw artifact, ingestion run and a **range-coverage status**:
`COMPLETE` (every expected session accepted), `PARTIAL` (some accepted, some rejected or missing, with the list) and
`FAILED` (none accepted). Namespaced as market-data *range coverage* (`RangeCoverageStatus`), deliberately not the
reproducibility `COMPLETE/PARTIAL` (which means evidence pinned to runs); the existing `StockSplitCoverage` table is the
precedent to follow. The current schema cannot truthfully represent this, so it needs a migration (post-RC).

Research interaction (conceptual proof): sessions and horizons come only from the exchange calendar
(`proven_sessions_after`); bars only supply closes by exact date. A partial ingestion therefore can supply endpoint
bars but can never redefine the session sequence: an accepted anchor and target bar yield a return even when interior
rows were rejected (the 11.2E rule H), and a missing endpoint yields `MISSING_ANCHOR_PRICE`/`MISSING_TARGET_PRICE`. Coverage
would only explain *why* a price is absent.

Concrete workflow requiring it today: none. The operator workaround (re-run a smaller range) exists and the one
real cause (VWAP containment) was removed in 11.2E. **Verdict: POST-RC.**

### 4.3 Reference and earnings raw retention - **RC REQUIRED**

Reference (`IngestionService.ingest_reference`, `MassiveClient.get_ticker_details`):
- `get_ticker_details` builds the capture list locally; if `_parse_ticker_details` raises, the list is lost and
  nothing is attached to the exception. An undecodable 200 is lost inside `_get_json` (no `capture_undecodable_as`
  for this dataset).
- The service catches `Exception` per ticker (`failed += 1; continue`) so even an exception that did carry
  captures would be dropped.
- Captures are accumulated in a local list and recorded only after the whole loop; any uncaught exception mid-batch
  (including the re-raised `ReferenceResolutionError`) discards the bytes of every ticker already fetched.
- Comparison with daily bars (repaired in 11.2E): the same abstraction is directly reusable: attach
  `exc.captures` in the client, and `store_rejected_captures(run, store, exc.captures)` in the service before continuing
  or re-raising; record per-ticker captures as they arrive.

Earnings (`EarningsIngestionService.ingest_earnings`, `FinnhubClient.get_earnings`):
- `_get_json` decodes inside `_Response(...)`; an undecodable 200 raises with no capture.
- `_parse_earnings_calendar` / `to_canonical_earnings_event` can raise after the capture is built but the capture is
  never attached to the exception, and the service has no `except ProviderError` branch (daily bars do).
- Ordering is otherwise right: on success captures are recorded before canonical mapping.
- Note: Massive/Benzinga earnings are entitlement-blocked and not wired; Finnhub is the only earnings path.

Reproducibility and auditability are core principles and successful provider bytes can still be lost, so
**RC REQUIRED** (P2). Fix shape: attach captures on parse/validation failure, store as `<dataset>-rejected`, record
incrementally. No migration, no API change.

### 4.4 Partial-run reasons - **RC REQUIRED (minimal)**

`ingestion_run` has `error_type`/`error_message` for failed runs only (redacted, 1000 chars). A `partial` run has only
`records_failed`; the reasons exist solely in `logger.warning` text. Observed: the dev database holds two `partial` earnings
runs with `records_failed=1` and no recorded cause anywhere. No schema change is needed: for a partial run populate
`error_message` with `"N record(s) failed; first: <redacted reason>"` (and a short capped list), using the existing redaction.
No universal ontology. A structured per-record rejection table belongs with the post-RC coverage model.

### 4.5 Dedicated missing-target status - **RC REQUIRED** (section 4.1)

### 4.6 P3 items

| Item | Reassessment | Scope |
|---|---|---|
| favicon 404 | missing static asset (`app/` has no `icon`/`favicon`); only console error on every page | **RC REQUIRED quick fix** |
| "Too few stored sessions" wording | resolved by 4.1 | RC (with 4.1) |
| calendar singleton first-build race | benign (build is idempotent, cached by UTC day); the first research request after start costs ~0.3 s (measured 295 ms) | POST-RC |
| header artifact `.json` extension | cosmetic, nominal extension of content-addressed bytes; metadata names the dataset | POST-RC |
| unreachable partial branch | dead code with a comment | POST-RC |

## 5. Operational readiness

### 5.1 Startup, liveness, readiness (verified)

| Condition | Behaviour today | Verdict |
|---|---|---|
| Postgres down | process starts (construction does no I/O); `/health` 200; `/health/ready` 503 naming `database`; every data route 503 generic | correct |
| Redis down | process starts; **readiness 503 although nothing uses Redis** (only readiness and shutdown touch it); data routes unaffected | **false not-ready: P2, RC REQUIRED** |
| malformed / non-Postgres DB URL | fails at startup, URL never echoed | correct |
| provider keys missing | API starts; only ingestion refuses (typed configuration error) | correct |
| `QF_DEBUG=true` with `production` | settings refuse | correct |
| default DB credentials with `production` | allowed | **P2, RC REQUIRED guard** |
| schema behind / ahead of the code | **no check anywhere** (no `alembic_version` read in `src`) | **P2, RC REQUIRED** |
| schema behind, a route touches a missing column | `ProgrammingError` is not mapped (only `OperationalError`/`InterfaceError`): generic 500 | covered by the schema check |
| DB hangs (not refused) | no `connect_timeout`, no `statement_timeout`, engine has only `pool_pre_ping` | **P2, RC REQUIRED** |

Liveness (`/health`, `/api/v1/health`) correctly never touches a datastore; readiness is DB + Redis. Proposed
readiness: `database` and a new `schema` check (installed revision equals the code's expected head; states
`ok | behind | ahead | unknown`) gate readiness; `redis` is reported but **non-gating** (`degraded`) until some feature
uses it. A constant `EXPECTED_SCHEMA_REVISION` guarded by a test equal to the Alembic head avoids shipping the
migration scripts with the API. Never auto-migrate in production (already the architecture: `alembic upgrade head` is manual).
Contract cost: `ReadinessResponse` gains `schema`; the frontend shell's system panel decodes readiness strictly, so the
decoder, fixtures and the Overview "System" panel move together.

### 5.2 Migration safety and rollback (documentation-critical)

`0014` downgrade refuses while any `sec_filing.accepted_at` is NULL, and bulk ingestion now creates NULLs by design. So
**rolling back past 0014 after any SEC ingestion is not possible by `alembic downgrade`**; the supported rollback is
restore-from-backup. This must be in the runbook. Deploy order: back up, `alembic upgrade head`, restart, readiness 200,
release smoke.

### 5.3 Configuration inventory

| Setting | Needed for | Classification |
|---|---|---|
| `QF_DATABASE_URL` | API + ingestion | required (default is dev credentials: must be explicit in production) |
| `QF_REDIS_URL` | readiness only today | optional |
| `QF_ENVIRONMENT`, `QF_DEBUG`, `QF_CORS_ORIGINS`, `QF_LOG_LEVEL` | API | required in production for explicit values |
| `QF_MASSIVE_API_KEY` (+ timeouts) | reference, daily, benchmark, splits, listing facts | ingestion only |
| `QF_FINNHUB_API_KEY` | earnings | ingestion only |
| `QF_SEC_USER_AGENT` | all SEC requests | ingestion only |
| `QF_INGESTION_RAW_ROOT` | ingestion + replay | required (raw store is part of reproducibility) |
| `NEXT_PUBLIC_API_BASE_URL` | web build and server render | required at **build time** (baked) |

`.env.example` is stale: it omits `QF_SEC_USER_AGENT` and the SEC/Massive paging settings.

### 5.4 Operator experience (CLI)

An operator can answer *what ran* (`runs`) but not reliably: *which run failed* (an exception prints `error: ...`
without the run id), *was raw evidence retained* (the failure path does not print the manifest path), *were rows written*
(failed runs roll back; `runs` shows counts but not the error or raw path), *is retry safe* and *what to rerun*. There
is also **no earnings command**. Minimum: on failure print `run <id> failed; raw evidence: <path|none>; written: 0; safe to retry: yes`
(retry is safe everywhere, see 5.5); extend `runs` with `--status`, the error text and the raw path; add a bounded
`ingestion earnings TICKER --start --end` over the existing service.

### 5.5 Retry and idempotency

All current ingestion is retry-safe: reference (match-or-create), earnings (digest dedup; a changed fact is a
new revision row, so a retry after a provider revision legitimately adds a row and the earnings list shows both), bars
(skip identical, conflict on different), SEC identity (identity compare only, header-set instants preserved), acceptance
(no-op if set, conflict on disagreement), FSDS (package revision refused), splits/listing facts (conflict). Nothing duplicates on
retry. No non-idempotent retry found.

### 5.6 Interrupted and stale runs

The run row is flushed inside the caller's transaction and only becomes durable when the caller commits; a failed run is
committed only when the failure was recorded. If the process dies at any point, the transaction rolls back and **no
`RUNNING` row can persist** (verified: neither database has one). Mid-run death after a raw write leaves orphaned
content-addressed files with no run row (harmless; they dedupe on retry). A death between the data commit and nothing
else cannot occur (data and run commit together). Therefore no stale-run recovery is needed; the runbook only needs a note
on orphan raw files and raw-root growth.

## 6. API hardening findings

- **Error taxonomy**: 422 validation (FastAPI shape), 404 not found, 409 (verify), domain refusals as 200, 503 generic, 500 generic. Consistent except the earnings endpoints (no repeat guard, 503 undocumented).
- **Database outage**: all nine data routes return the generic 503; readiness 503; liveness 200 (verified by pointing the app at a closed port).
- **Leakage**: no SQL, DSN, password, path or exception text in any response. Log hygiene: readiness probes log a full traceback on every failure (`exc_info=True`) - noisy under an orchestrator probing every few seconds, and driver text could reach logs; the redaction claimed in `runs.py` for logs is not implemented as a logging filter (the code logs class names elsewhere). P3: log the class name only.
- **Pagination**: bounds enforced, stable total orders with tie-breakers, past-end pages empty, no gap/repeat (pinned by existing scale tests at 200 rows).
- **Repeated params**: guarded on all endpoints except the two earnings endpoints (**P2, RC REQUIRED**, a one-line dependency).
- **Determinism**: every list has a unique tie-break; unchanged data yields identical pages.
- **Security boundaries**: the API has no authentication and is read-only; it must be deployed on a private network or behind a reverse proxy. This is a documented deployment constraint, not a feature to add in Step 12.

## 7. Frontend RC audit (production build, real data)

- **State model**: every page distinguishes loading (route `loading.tsx`), empty, not found, invalid URL state ("This configuration cannot be used. No request was made."), domain refusal, backend unreachable ("Cannot reach the QuantForge API"), database unavailable ("Temporarily unavailable ... database cannot be reached") and unexpected error (`error.tsx`). Verified by killing the API and by running the API against a closed database port.
- **Deep links**: event, research, filing, saved record all render from a cold context.
- **URL state**: malformed UUID/benchmark/horizons/page/CIK/form/period/EPS kind show a specific message and make no request; repeated parameters are rejected; unknown parameters on the research page are silently ignored (benign; the backend would reject them if forwarded, but the frontend never forwards them).
- **Soft 404**: not-found states render HTTP 200 (only unmatched routes are 404). P3, post-RC.
- **Prefetch**: all internal content links go through the `AppLink` wrapper (`prefetch={false}`). The sidebar `NavLink` uses `next/link` directly; measured no API read from it after 3.5 s idle on four pages (counts equal the budgets), so P3: align it with the wrapper for policy consistency (one line).
- **Exact values**: `Number(`/`parseInt(` appear only for pagination, calendar parts, horizon integers and `chart/geometry.ts` plotting; no finance value is coerced for display. Pass.
- **Wording**: no growth/recommendation/signal language anywhere (only explicit disclaimers). One real overclaim: the listings table column **"Valid from"** shows the *first-observed* date (2026-10-07) beside "Valid on the event date: Valid" for 2026-10-01 - it reads as a contradiction and as a claim that the listing began on 2026-10-07. P2 wording, **RC REQUIRED**: rename to "First observed" with a one-line note (label only; no API change).
- "Too few stored sessions" is the semantic issue of 4.1.

## 8. Security / privacy

- **Secrets**: provider keys travel in headers only (Massive `Authorization`, Finnhub `X-Finnhub-Token`, SEC `User-Agent`); never in URLs, captures, run rows (redaction of query strings, key assignments and bearer tokens before persistence) or the repository (scanned each checkpoint).
- **Raw artifact paths**: allow-listed providers (`finnhub`, `massive`, `sec-edgar`), segment regex `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, key segment hashed from parameters, a `resolve()` + `is_relative_to` guard on reads; accessions/symbols never reach a path unvalidated. Pass.
- **Input bounds**: ticker (regex, <= 32/64), CIK 1-10 digits, accession pattern, UUID, horizons (<= 10, 1-252), limit/offset, enumerations for form/period/EPS kind. Pass.
- **CORS**: default origin `http://localhost:3000`, credentials **true**, methods include `POST/PUT/PATCH/DELETE`, headers include `Authorization` - for a read-only, auth-less API. Not a wildcard, but production should be explicit: **P3, RC REQUIRED in 12D** as a production guard (explicit origins required, credentials default false, methods `GET, OPTIONS`).
- **Debug**: `debug` refused in production; docs/openapi disabled in production; generic 500 body; no SQL echo.
- **Dependency audit**: not performed in 12A; scheduled in 12E (`pip-audit`, `npm audit`).

## 9. Performance baseline (validation DB; warm; TestClient, in-process)

| Call | Warm ms | Statements | Bytes |
|---|---|---|---|
| earnings list (1 row) | 7.8 | 7 | 1,206 |
| earnings detail | 7.2 | 6 | 1,162 |
| benchmarks | 3.0 | 2 | 178 |
| filings list | 4.4 | 2 | 206 |
| filing EPS | 4.1 | 3 | 1,420 |
| research, 1 horizon + SPY | 11.1 | 10 | 4,834 |
| research, 3 horizons | 10.1 | 10 | 6,531 |
| research, 10 horizons | 9.7 | 10 | 13,146 |
| research default stock-only | 8.4 | 8 | 6,438 |
| saved list | 3.3 | 2 | 622 |
| saved detail | 2.7 | 1 | 13,679 |
| verify | 9.3 | 13 | 1,153 |
| max page (limit 200): earnings / filings / records | 5.6 / 3.1 / 2.8 | 7 / 2 / 2 | tiny (1 row each) |
| health / ready | 0.9 / 2.5 | 0 / 1 | - |

First research call after process start: 295 ms (calendar build; the benign singleton). Research statement count is flat in
the number of horizons (10), confirming 11.2E. Limits of this baseline: one event and one saved record; scaling is
covered by the pinned tests (filings index 200-row page and two-statement list, records page, earnings list count flat,
research horizon flatness). Saved-list current-event enrichment is one event lookup per distinct event on a 20-row page
(worst case 20 x 6 statements, about 150 ms server-side): bounded by design, not N+1 inside one request; a batch endpoint is post-RC.

## 10. Documentation, runbook, backup

- **README**: the quick start works (compose, `alembic upgrade head`, uvicorn, `npm run dev`) but the "Current state" table
  is stale (still lists the 0006 working tree), the migration section says head `0012`, there is no earnings ingestion
  command to run, `.env.example` lacks the SEC variables, and apps are not containerised (no Dockerfile, no CI config).
  A new engineer cannot ingest a bounded sample end to end without tribal knowledge. **RC REQUIRED.**
- **Runbook**: absent (deploy, migration, rollback, health verification, smoke, provider outage, failed ingestion, restore).
- **Backup/restore**: absent. Minimum operator procedure: `pg_dump -Fc` (or a volume snapshot) of the database **and** a copy of
  `QF_INGESTION_RAW_ROOT` taken together (database rows reference raw artifacts by relative path, and verification replays
  stored evidence, so a database restored without its raw store loses reproducibility); restore with `pg_restore` into an
  empty database, then `alembic current`, readiness, release smoke. Never `docker compose down -v` (documented).
- **Validation database**: `quantforge_validation` and `scripts/validation/*` are validation/test infrastructure, not a production dependency; never
  point production at it and never ship its raw root.

### Release smoke (design)

Required state: an API pointed at a database holding at least one earnings event, one FSDS-backed filing and one saved
record; the production web build served against it. Command: `node scripts/validation/real_smoke.mjs` (read-only; discovers
identities from the API; verification only with `REAL_SMOKE_VERIFY=1`) plus a short API probe script (health 200, ready 200,
each list 200, one event/filing/record detail 200). Pass: every probe 200 with the expected shape, zero axe violations, no
horizontal overflow at 1440 and 390 px, and no 5xx in the access log. Fail: any non-200 probe, any axe violation, or a ready 503.

## 11. Finding register

| # | Finding | Sev | Scope | Slice |
|---|---|---|---|---|
| 1 | proven-target-without-price reported as `INSUFFICIENT_FORWARD_SESSIONS` / "Too few stored sessions" | P2 | RC REQUIRED | 12B |
| 2 | earnings endpoints accept repeated scalars (detail also ignores unknown params); 503 undocumented in OpenAPI | P2 | RC REQUIRED | 12B |
| 3 | "Valid from" label shows first-observed date | P2 | RC REQUIRED | 12B |
| 4 | favicon 404 | P3 | RC REQUIRED (quick) | 12B |
| 5 | `NavLink` bypasses `AppLink` (no read observed) | P3 | RC REQUIRED (one line) | 12B |
| 6 | reference raw bytes lost on parse failure / mid-batch error | P2 | RC REQUIRED | 12C |
| 7 | earnings raw bytes lost on parse failure / undecodable 200 | P2 | RC REQUIRED | 12C |
| 8 | partial runs carry no reason | P2 | RC REQUIRED | 12C |
| 9 | no earnings CLI; CLI failure omits run id / raw path; `runs` lacks error and raw path | P2 | RC REQUIRED | 12C |
| 10 | readiness gates on unused Redis | P2 | RC REQUIRED | 12D |
| 11 | no schema-revision check | P2 | RC REQUIRED | 12D |
| 12 | no DB connect/statement timeouts | P2 | RC REQUIRED | 12D |
| 13 | production allowed on default DB credentials; permissive CORS defaults | P2 / P3 | RC REQUIRED | 12D |
| 14 | README stale; no runbook; no backup/restore; `.env.example` stale | P2 | RC REQUIRED | 12D |
| 15 | readiness logs full traceback per probe; log-redaction claim unimplemented | P3 | RC REQUIRED (small) | 12D |
| 16 | per-record partial bar storage + coverage model | P2 | POST-RC | - |
| 17 | soft-404 HTTP 200 on not-found states | P3 | POST-RC | - |
| 18 | calendar singleton race; `.json` header extension; dead partial branch | P3 | POST-RC | - |
| 19 | batch endpoint for saved-list event enrichment | P3 | POST-RC | - |
| 20 | no authentication / rate limiting (deployment constraint) | - | out of scope, document | 12D |

P0: none. P1: none. RC blockers: none; the RC-required set is findings 1-15.

## 12. Proposed sequence

**12B - semantic and API consistency.** Goal: remove user-visible imprecision. Scope: `MISSING_TARGET_PRICE` + methodology
`forward_returns/2` (section 4.1); repeated-parameter guard and OpenAPI 503 on the two earnings endpoints; "First observed"
label; favicon; `NavLink` through `AppLink`. Migration: none. API contract: one enum value (+ OpenAPI 503 docs). Frontend: wording,
state map, schema list, fixtures. Tests: updated/added backend and frontend tests, contract export, fixture E2E, a new golden
record saved in the validation DB. Exit: goldens identical except the explained `+5` relabel; old record `MANIFEST_CHANGED`.
Risk: medium-low.

**12C - ingestion provenance hardening.** Scope: attach captures to provider exceptions in `get_ticker_details` and Finnhub
`get_earnings`; store `-rejected` evidence in both services; record reference captures incrementally; partial-run reason summary in
`error_message`; `ingestion earnings` command; failure output with run id/raw path; `runs --status` with error/raw columns. Migration:
none. API/frontend: none. Tests: raw-retention matrices mirroring the daily-bar tests, CLI output tests. Exit: no path loses a
successful response; every non-succeeded run states a reason. Risk: low-medium.

**12D - operational readiness.** Scope: readiness `schema` check + non-gating Redis; `connect_timeout`/`statement_timeout`; production
guards (explicit DB URL, explicit CORS, credentials false, GET/OPTIONS); probe logging; README refresh, `.env.example`, runbook
(deploy, migrate, rollback = restore, health, smoke, provider outage, failed ingestion), backup/restore, release-smoke script,
deployment constraints. Migration: none. API contract: readiness field. Frontend: shell/Overview readiness decoder. Risk: medium (touches
the readiness contract).

**12E - RC regression, performance, security.** Scope: full backend/frontend gates, fixture E2E (205) and live smoke, goldens
(market, accounting, session alignment, saved verify), dependency audit, secret and leakage scan as tests, a synthetic larger-dataset
performance script (list pages, saved enrichment, research), the recorded baseline, an `rc-gate` script that runs everything locally.
No new features.

**12F - release-candidate freeze.** Real +5 / +20 recheck if their sessions exist (section 13), final review, commit, tag.

No migrations are planned in Step 12; partial-bar coverage (which needs one) stays post-RC.

## 13. RC acceptance criteria

P0 = 0; P1 = 0; every finding 1-15 resolved or explicitly waived; backend full suite green (including migration lifecycle, `alembic check`);
frontend lint, typecheck, unit, build and contract green; fixture E2E green; release smoke green; secret scan, leakage tests and dependency
audit clean; performance baseline recorded; runbook, backup/restore and README verified by following them on a clean database; clean tree;
market golden (anchor 2026-10-02, +1, +2, abnormal values, PIT refusal, COMPLETE, saved MATCH/expected MANIFEST_CHANGED) and accounting
golden (0.3500 / 0.5400 / -0.1900 / -0.3518 / POSITIVE_DECREASED, identical with `accepted_at` NULL or set) unchanged.

## 14. Future real-data validation (outside RC implementation)

- **+5** (target 2026-10-09) and **+20** (target 2026-10-30): run the section 23.1 procedure of `docs/production-validation-design.md`
  only after the session has completed and the provider has published it. Not run in 12A; no provider request was made. If either
  disagrees with the tested calendar semantics, treat it as new evidence and reopen the affected component; otherwise record it as
  additional golden evidence.

## 15. Step 12B result (semantic and API consistency; appended, sections 0-14 are the unchanged 12A audit)

12A was committed as `551ec6c`. 12B implements five bounded changes on top of it. No migration (head stays `0014`), no ingestion,
readiness or configuration change, and no provider request (Massive 0, Finnhub 0, SEC 0; the validation ceiling was set to 0).

| change | result |
|---|---|
| Missing-target status (finding 1) | `ForwardReturnStatus.MISSING_TARGET_PRICE`: the calendar proves the N-th session, no price is stored for it; the proven `target_session` is reported with no close and is never moved. `INSUFFICIENT_FORWARD_SESSIONS` now means only that the calendar cannot prove the N-th session (`target_session` is `None`). The two statuses are told apart by a structural invariant that a test asserts. No wall-clock branch, no "future" status. |
| Methodology | `forward_returns` `close-to-close-proven-session-returns/1` -> `/2` (one place, pinned by `test_methodology_table_is_pinned_so_a_semantic_edit_is_deliberate`). |
| Earnings API (finding 2) | Repeated scalar parameters are 422 on the list and the detail; the detail rejects every query parameter (422, existing `NoQuery`); both operations document the generic 503 (body unchanged). |
| Listing label | "Valid from" -> "First observed" plus one note that it does not date the listing, ticker or exchange relationship. Wording only. |
| Sidebar | `NavLink` renders through `AppLink`; only `app-link.tsx` imports `next/link` (structure test). |
| Favicon | `src/app/icon.svg` (Next file convention, no package). |

**Downstream.** Every consumer already branched on `is AVAILABLE` (abnormal, benchmark leg, signals, manifest bar pinning, observation
refusals, event study), so a missing stock target price produces no return, abnormal return, signal or bar pin, and the refusal code
(`status.value`) carries the new status into the MARKET and POINT_IN_TIME scopes with no new taxonomy. The benchmark leg's own
`MISSING_TARGET_PRICE` is untouched.

**Golden regression (validation DB, offline).** The saved record `e880eaf9-...` now verifies `MANIFEST_CHANGED` with the single changed path
`methodology.components[7][1]` (forward_returns); its row digest is unchanged and the table forbids UPDATE/DELETE. The reassembled
observation is arithmetically identical to the frozen golden (anchor 2026-10-02; stock +1 0.0026572, +2 0.0218482; abnormal +1
-0.0040862, +2 0.0095698; PIT `INELIGIBLE_NO_AVAILABILITY_EVIDENCE`); `+5` is now `MISSING_TARGET_PRICE` with the proven target 2026-10-09.
One new record `ed7a3a9b-c467-4e10-b537-0504b722ece3` was saved through the production save path and verifies `MATCH`; both records
coexist. The accounting golden is unchanged (0.3500 / 0.5400 / -0.1900 / ratio -0.3518 displayed / `POSITIVE_DECREASED`, `accepted_at` set).

**Gates.** Backend 3434 passed (2423 unit + 1011 integration), 95.54 % coverage; ruff, format, mypy clean; `alembic check` clean at `0014`.
Frontend 1410 unit tests, lint, typecheck, production build and `contract:check` green; fixture E2E 205 passed (frozen request budgets
unchanged: 5 / 2 / 3 / 2 / 2 / 4 / 2 / 3), plus an added sidebar hover/focus test (no request) and a favicon test.

**Fixtures.** The six `INSUFFICIENT_FORWARD_SESSIONS` entries in `tests/contract/{observation,records}.json` all have `target_session: null`
(the calendar-short shape) and are correctly unchanged; the new status is covered by decoder tests that mutate a captured record.
Observation: those captures predate 11.2E (their reason text is the old "fewer than N available trading sessions" wording); a recapture is
a P3 follow-up outside 12B.

**Review.** One high-effort review: no P0/P1/P2. Two stale test names/comments fixed. Out of scope, recorded: `docs/frontend-v2-design.md`
(lines ~1780, 1818) still lists the old enum and wording (frozen design document); "First observed" now sits beside "Valid to" (the
note covers it; `valid_to` is a separate concept).

Next planned slice remains 12C (ingestion provenance hardening: findings 4-8).

## 16. Step 12C result (ingestion provenance hardening; appended, sections 0-15 are unchanged history)

12B was committed as `7dd6d59`. 12C implements findings 4-8 of the register with no migration (head stays `0014`), no research,
reproducibility, frontend, readiness or configuration change, and no provider request (Massive 0, Finnhub 0, SEC 0).

**Invariant.** If provider response bytes reached QuantForge, they end as accepted evidence of a normal ingestion or as retained rejected
evidence under `<dataset>-rejected`; they are never discarded and never rebuilt from parsed JSON.

**Where bytes used to disappear (audit, before the change).** Reference: an undecodable 200 (no capture requested); a decoded response that
failed ticker-details validation (the capture existed only in a local list that the exception did not carry); canonical or resolution
failure (bytes kept, but filed as *accepted*); every capture held in memory until after the whole ticker loop (a later unexpected failure
dropped them all). Earnings: an undecodable 200; a validation failure and a canonical-mapping failure (capture built, never attached);
no handler in the service that stores rejected bytes. Proof: 13 of 18 new provenance tests failed on the unchanged code, and in each the
run had no manifest at all.

**What changed.**

| area | change |
|---|---|
| Massive `get_ticker_details`, Finnhub `get_earnings` | undecodable 200 and every later refusal raise with the exact response bytes attached (`ProviderError.captures`); a pathologically nested body (`RecursionError`) is now a refused response too |
| `RunRecorder` | manifest is cumulative over everything kept; `add_artifacts` keeps already-durable artifacts and the manifest is written at exit on success **and** failure, so a failed run still points at what it kept; `record_captures` unchanged for existing callers |
| reference service | per-ticker retention: accepted evidence is made durable as soon as that ticker's outcome is known; a refused response (client, canonical or resolution) is rejected evidence; an unexpected failure keeps the held response as rejected evidence then fails the run; per-ticker failure semantics unchanged |
| earnings service | a provider refusal stores its bytes as `earnings-rejected`, then fails the run as before; a valid response with unattributable events stays accepted and the run is `partial` |
| partial reasons | `error_message` = `N record(s) failed; first: <label>: <reason>` (redacted, capped to the 1000-char column, first in deterministic processing order), `error_type` = the first failure's class; success rows have none; failed rows are produced exactly as before |
| redaction | now also URL userinfo (DSN passwords), e-mail addresses (SEC contact identity) and absolute paths under common roots |
| CLI | `ingestion earnings TICKER --start --end`; a failed command prints a footer (`run <id> [provider/dataset] failed`, `raw evidence: <path|none>`, `written: N`, `safe to retry: yes`, `reason:`) after the error line; `ingestion runs --status <enum>`; `runs` shows the run id, `raw manifest:` and a bounded error |

**Namespace rule (defensible, recorded).** *Accepted* = a provider payload that was valid and handed to ingestion, even if some of its events
could not be attributed; *rejected* = a response the adapter or the canonical/resolution step refused. A failed run's accepted artifacts are
real provider bytes of a rolled-back ingestion; the manifest lists them by path namespace only.

**Raw path truth.** `ingestion_run.raw_artifact_path` is the run's **manifest**, not a payload: a run can keep many artifacts. A run that kept
none shows `none`.

**Retry claim.** `safe to retry: yes` rests on the 12A audit (a failed run rolls its writes back; every write is idempotent; raw artifacts
are content-addressed; the manifest key includes the run id). It means a retry will not duplicate data, not that it will succeed (a
conflict or an unattributable event fails again). Pinned for the two commands 12C changed.

**Gates.** Backend 3524 passed (2460 unit + 1064 integration), 95.90 % coverage; ruff, format, mypy clean; `alembic check` clean at `0014`.
Frontend untouched (1410 tests, lint, typecheck, build, contract check green). Goldens unchanged: `e880eaf9` verifies `MANIFEST_CHANGED`
(`methodology.components[7][1]` only), `ed7a3a9b` verifies `MATCH`, market and accounting arithmetic identical.

**Review.** One high-effort review: no P0/P1. Fixed: RecursionError escaping both clients with no captures (P2); DSN passwords containing
`@`/`?` and more absolute-path roots (P3); `captures` handling moved inside the best-effort `try` (P3); an overclaiming test name (P3).

**Known limits, recorded (not fixed in 12C).**
- Reference bytes are written once their accepted/rejected outcome is known, which is after interpretation; a hard crash (SIGKILL, power
  loss) between receipt and that point loses them, while every handled exception is covered. Earnings stores before ingesting.
- A response larger than the raw store's limit is logged, not retained as rejected evidence.
- A raw `/` in a DSN password is indistinguishable from the start of the URL path and is not redacted (real DSNs percent-encode it).
- The invariant is not yet global: Massive stock-splits, ticker snapshots and ticker events, and the SEC client, still drop an undecodable
  200's bytes. Out of 12C by scope; carry to a later provenance slice.
- Ctrl-C still commits the failed row but prints no footer (unchanged exit behaviour).
- A per-artifact `disposition` field in the manifest was considered and left out (it would change the manifest format).

Next planned slice: 12D operational readiness (schema check, Redis tolerance, DB timeouts, production guards, README/runbook, backup).

## 17. Step 12D result (operational readiness and release operations; appended, sections 0-16 are history)

12C was committed as `42f48cf`. 12D implements findings 9-15 of the register and the readiness decision (schema check and
non-gating Redis are in the machine-readable response, not a runbook-only check). No migration (head stays `0014`), no research,
ingestion, accounting or provider change, no provider request (Massive 0, Finnhub 0, SEC 0).

**Before (audit + proof tests, run on the unchanged code).** Liveness already touched nothing. Readiness gated on Redis (a Redis
outage made it 503), checked only `SELECT 1` (an empty database with no `alembic_version` was "ready"), logged a full traceback on
every expected failure, and its docstring claimed a redaction that did not exist. The frontend's Overview read the flat
`{database, redis}` shape and rendered a Redis outage as an error.

| area | result |
|---|---|
| Liveness | unchanged behaviour, now pinned: runtime explode-on-touch test over every datastore/migration symbol, an AST import allow-list for `api/v1/health.py`, and a test that no raw-root path is created |
| Readiness | `200` only if PostgreSQL answers a bounded query **and** the Alembic revision equals the binary's single head exactly; behind, ahead, no table, empty, several rows, several heads, unreadable scripts all `503`. Response: `status`, `service`, `dependencies.{database,redis}.{state,gating}`, `schema.{state,expected,current,gating}`, `detail` |
| Head derivation | one mechanism: `ScriptDirectory` over `apps/api/alembic` (`db/schema.py`), cached; nothing hand-maintained. A real `alembic upgrade head` is proven to stamp the same value |
| Redis | `ok / unavailable / not_configured`, `gating: false`; a blank `QF_REDIS_URL` means not configured; a malformed URL is `unavailable`, never a 500 (review finding) |
| Providers | never probed; asserted at runtime (no real network transport may be used) and structurally (no provider/ingestion import) |
| Timeouts | libpq `connect_timeout` (default 5 s, 1-60) and server-side `statement_timeout` (default 60 s, 1-3600) as driver arguments; `pool_timeout` follows the connect timeout; URL-supplied `options`/`connect_timeout` are respected; readiness caps its own statements at 5 s; migrations use their own engine |
| Production guards | refuse the development database logins (also percent-encoded, passwordless, or `?password=`), require explicit non-wildcard `QF_CORS_ORIGINS`, keep refusing `QF_DEBUG`; provider keys, SEC identity, Redis and raw root are never required to serve |
| CORS | methods `GET, OPTIONS` (the API is read-only; no HEAD routes exist), credentials default off |
| Logging | expected outages are one concise warning with class names only; unexpected defects keep their traceback; the docstring now promises only class names |
| Frontend | strict decoder (gating flags are literals); Overview: Redis down = "Unavailable · not required" and "Ready"; schema mismatch = generic "Not ready", no revision text; no new request |
| Docs / ops | `.env.example` classified (serve / ingest / dev), README refreshed (head `0014`), `docs/operations-runbook.md`, `scripts/release-smoke.mjs` over `real_smoke.mjs` (which now exits non-zero on any failed criterion) |

**Consequence to know.** The development database `quantforge` is deliberately still at revision `0013`; under this contract an API
pointed at it reports `503 schema: mismatch` until `alembic upgrade head` is run on it. That is the check working.

**Gates.** Backend 3613 passed (2532 unit + 1081 integration), 95.93 %; ruff, format, mypy clean; `alembic check` clean at `0014`.
Frontend 1444 unit tests, lint, typecheck, build, contract check green; fixture e2e 207 passed with the request budgets unchanged.
Release smoke against the real stack (validation database, production build): liveness, readiness, web, 9 pages x 2 viewports,
unchanged-state check all pass; stopping the backend fails it with `FAIL liveness` and exit 1. Goldens unchanged.

**Review.** One high-effort review: no P0/P1; one P2 (a malformed Redis URL made readiness 500) fixed with a test. Fixed P3s:
production-guard bypasses (percent-encoding, no password, `?password=`), connect args overriding URL options, a stale session factory,
an unbounded pool wait, `fullmatch` for revision ids, HEAD advertised but unserved, a vacuous test clause, smoke-wrapper edges (browser
timeout, stale-port detection, build pointed at the API under test, process-group shutdown), and runbook gaps (serving role needs
SELECT on `alembic_version`, guards apply to alembic/CLI shells, stop the API before replacing the tree, source/editable install only,
blank Redis URL, macOS `shasum`).

**Carried (not fixed in 12D).** The smoke passes a page that renders an error state with HTTP 200 (SSR errors are soft; soft-404
conversion is deferred); its `unchanged` phase compares four totals; poolers that reject the `options` startup parameter need
`ignore_startup_parameters=options` or a direct connection; the ingestion CLI inherits the 60 s statement timeout (raise
`QF_DB_STATEMENT_TIMEOUT_SECONDS` for a deliberately long bulk run); a wheel install cannot establish its head and stays not ready.

Next planned slice: 12E (regression, performance and security cleanup; frontend fixture recapture; dependency and secret audits).
