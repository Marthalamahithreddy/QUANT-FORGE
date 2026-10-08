# Release qualification report (Steps 12E and 12F)

Baseline: `8661fba7b935df047a0609e6664b2667416c1aaf` (Step 12D, "fix: harden operational readiness"), migration head `0014`,
validation database `quantforge_validation` at `0014`, development database `quantforge` at `0013` (untouched).
Provider requests during qualification: **Massive 0, Finnhub 0, SEC 0** (no request ledger was created). The only outbound
requests were the package-advisory lookups of the two dependency audits (OSV through `uv audit`, the npm registry through `npm audit`).

12E is a qualification slice. No migration, provider, research methodology, accounting semantics or ingestion algorithm changed.
The only production source change is one line of presentation in `session-ladder.tsx` (section 5) and the `next` patch upgrade (section 3).

## 1. Release smoke

**Pre-fix proof.** With every event-detail request answered `503` (the fixture API, `e2e/fixture-api.mjs`, in front of the e2e production
build) the 12D smoke printed `RESULT: PASS`, exit 0: the event and research pages rendered "Temporarily unavailable" as an ordinary page with
HTTP 200 and zero axe violations. The same condition now prints `FAIL browser: 6 failure(s): desktop earnings-event: application error state
rendered with HTTP 200 (1 role="alert" marker(s); ...)` and exits 1.

**What changed.** `scripts/validation/page_health.mjs` classifies a page by the design system's own markers, not wording: `role="alert"` and
`data-state="ERROR"` are reserved for application failure; refusals (`REFUSED`), `MISSING`, `NOT_REQUESTED`, `UNAVAILABLE` and honest empty lists are domain
states and pass. Each page is also judged against what the API reported it holds (a list the API says has rows must have rows and the same total; headings,
sections, the chart and the overview "Ready" indicator must be present). Uncaught script errors, console errors, any browser write request and navigation
failures fail too. The `unchanged` phase compares per-list digests plus, with `--db-fingerprint`, a per-table database fingerprint.
It also fixed a latent false positive: two reads that both failed used to compare equal ("nothing" == "nothing"); an unreadable state is now a failure.

**Failure injection (all exit non-zero and name the phase / page).**

| Injected | Where proven | Result |
|---|---|---|
| backend stopped | real stack | `FAIL liveness: ... ECONNREFUSED`, exit 1 |
| database unreachable | real API pointed at a dead port | `FAIL readiness: HTTP 503, not ready: database unavailable, schema unknown` |
| schema mismatch | real API on the dev DB (0013), read-only | `FAIL readiness: ... schema mismatch (database at 0013, expected 0014)` |
| web stopped | real stack | `FAIL web: ... ECONNREFUSED` |
| API outage / DB outage / invalid response / render-boundary error / ERROR badge, all served 200 | real Chrome vs stub site (`e2e/release-smoke-controls.spec.ts`) and real build vs fault-injected fixture API | `application error state rendered with HTTP 200` |
| empty state where the API reports rows | same | `expected at least 1 row(s) because the API reports data, found 0` |
| unexpected 5xx; non-200 page; script error; console error; browser POST | same | each named, exit 1 |
| horizontal overflow; axe violation | same | `horizontal overflow`, `1 axe violation(s): image-alt` |
| mutation during the run | real DB copy mutated while a real `release-smoke --db-fingerprint` ran; stubs for the HTTP level | `stored state changed during the run at: db.daily_price_bar.head, ...` / `db.ingestion_run.writes.1` / `db.earnings_event.writes.1` (a value-neutral rewrite is seen too); item content changed with every total equal is seen at the HTTP level |
| browser hang | orchestration test | `FAIL browser: the browser phase timed out` |
| controls | same spec (20 scenarios in all) | a healthy site passes; legitimate refusals / missing / unavailable states still pass; an event with no computed stock return legitimately has no chart and passes; a chart missing although the API reports a return fails |

Spawned processes: the server a run starts is stopped in `finally` (process group); after every run no `next`, `uvicorn` or Chrome process of the run remained.

**Non-mutation audit.** Every request the smoke script itself issues is a `GET` (counted: 22 in a full run); the browser's requests are counted per method
(`SMOKE REQUESTS {"GET":210}`) and any non-GET fails the run; source tests forbid write verbs, migrations, ingestion and saved-record creation; the verify page is
visited only with `--verify`. `unchanged` proves: no served list, no saved record and (with `--db-fingerprint`) no table was written during the run (row digests of
bounded windows, exact counts under 1,000,000 rows, PostgreSQL insert/update/delete counters for the whole table, saved records / raw-artifact references / ingestion runs in full).
It does not prove: unchanged values outside the digest windows of a very large table beyond what the counters say; state before the run; or that the counters were not reset.

## 2. Frontend fixture recapture

`observation.json` and `records.json` were recaptured with their existing recipes (`apps/web/scripts/capture_*_fixtures.py`: the current backend through
`TestClient` on a real PostgreSQL test database; no provider, no hand edit). The validation database holds one event, so it cannot supply the scenario
variety these fixtures pin (refusals, filing links, comparatives); the recipe is the established route, and the real golden event is cross-checked
against the live validation API (section 6). `earnings.json` and `vertical.json` contain no market-return content and were not regenerated.
The observation recipe gained one case, `horizons_beyond_calendar` (an event two sessions before the end of the exchange calendar's range), because the calendar only fails to
prove a session there; that is the only way to capture a real `INSUFFICIENT_FORWARD_SESSIONS`. Fixtures now carry `capture_baseline` metadata
(`step`, `forward_returns_methodology: close-to-close-proven-session-returns/2`, `provider_requests: 0`) and `contract-structure.test.ts` pins it, the methodology agreement with
the captured saved records, and the absence of every pre-11.2E / pre-12B phrase. Exact-string financial values are unchanged in kind (all `Decimal` strings).
Captured: `MISSING_TARGET_PRICE` x6 (observation), `INSUFFICIENT_FORWARD_SESSIONS` x1, `MISSING_ANCHOR_PRICE` x6; alignment is `ALIGNED` for every dated event (sessions come from the calendar).
Request budgets are unchanged. Tests that had encoded the old semantics were rewritten (not loosened); `docs/frontend-v2-design.md` was updated.

## 3. Dependency and security audit

| Audit | Tool / manifests | Result |
|---|---|---|
| Python | `uv audit` 0.12.19 (OSV), `apps/api/uv.lock`: 57 packages (43 runtime) | **0 vulnerabilities**, 0 adverse project statuses |
| npm, runtime | `npm audit --omit=dev` 11.19.0, `apps/web/package-lock.json` | before: 3 high (`next`, `sharp`, `source-map-js`); **after: 0** |
| npm, all | `npm audit` | 5 high remaining, **one root cause, development tooling only** |

*Remediated.* `next` 16.3.6 -> **16.3.8** (patch; pinned exact as before) fixes six advisories (affected `>=16.0.0 <16.3.8`): GHSA-cjq9-62q9-8jv4 SSRF in Image Optimization (high),
GHSA-4jqv-mc3x-m676 / GHSA-mcj8-r9mp-w47p cache poisoning of SSG/ISR pages, GHSA-3w37-wq28-93x7 `use cache` / Draft Mode leak, GHSA-f87g-xv8r-7p7x metadata image routes,
GHSA-39w2-rjm5-chcv dev-server MCP endpoint. Reachability of the deployed app (before the fix): every application route is dynamic (`ƒ`), no ISR/SSG data pages, no `use cache`, no Draft Mode,
no `next/image` use, no remote image patterns; so exposure was low, but the fix is a patch and costs nothing. The lockfile also moved `sharp` 0.35.4 -> 0.35.5 (image optimization only; fixes
GHSA-wq5f-xc86-pv6w) and `source-map-js` 1.2.1 -> 1.2.2 (build-time CSS tooling; GHSA-68fv-2mgg-jv7q), both within their declared ranges. Build, typecheck, lint, 1511 unit tests and the e2e suite were rerun on 16.3.8.
*Waived (documented).* `braces` 3.0.3 (GHSA-vfj7-8cjw-p6xm, stack exhaustion on deeply nested brace patterns, **no patched version exists**) under `micromatch` <- `fast-glob` <- `@next/eslint-plugin-next` <- `eslint-config-next`.
It is a development dependency (ESLint file globbing of this repository's own paths), is not installed in a production tree, and cannot receive untrusted patterns; `npm audit fix --force` proposes
*downgrading* `eslint-config-next` to 14.2.35, which is not a remediation. Revisit when `braces` publishes a fix.

*Other security checks.* Secrets: no key, token, private key or credential in any tracked file (regex scan of all 578 tracked files; the only `.env` value in git is the documented local-dev placeholder password; the Massive, Finnhub
and SEC identity values from `.env` appear in no tracked file and no commit). Tracked artifacts: only `.env.example` files, no logs/dumps/archives/`.next`/`node_modules`. Configuration guards (production refuses dev logins, unset or wildcard CORS, `QF_DEBUG`),
CORS (`GET, OPTIONS`, credentials off, a foreign origin gets no allow-origin header) and log redaction are pinned by the 12D tests. New in 12E (`test_deployment_boundary.py`): every OpenAPI route is `GET` and every write method is `405`; serving
requests never imports a provider or ingestion module; an unhandled error returns a bare 500 with no message, path or SQL. Input limits verified live: `limit<=200`, `horizons` <= 64 characters, bounded `offset`, over-long URLs refused, path traversal in identifiers is `404`/`422`; SQL metacharacters are inert. Raw-artifact path
traversal: `RawResponseStore` resolves every path and refuses anything outside the root (tests `test_read_refuses_to_escape_the_root` and the ticker-segment sanitiser). **Deployment boundary:** the API has no authentication and must run on a private network or behind an authenticating reverse proxy (runbook section 10).
Redaction edge cases found (no emitter in the code base, P3): `password='quoted value'`, JSON `"api_key": "..."` / `"token": "..."` and `client_secret=` are not matched by `ingestion/runs.py`; no provider client puts a response body or such a field in an error message.

## 4. Performance

Method: `scripts/validation/perf_baseline.py` (in-process, exact SQL statement counts, 30 warm runs) and `scripts/validation/page_perf.mjs` (production build, HTTP and Chrome). Same call set and database as the 12A baseline.
Compared with 12A (design doc section 9): **statement counts are identical for every call** (earnings list 7, detail 6, benchmarks 2, filings 2, EPS 3, research 8 / 10 / 10 / 10 for default / 1 / 3 / 10 horizons, saved list 2, detail 1, verify 13);
warm medians are the same or lower (earnings list 7.8 -> 5.0 ms, research 10.1 -> 7.0 ms, verify 9.3 -> 8.4 ms; a different machine state, no regression). Readiness is 3 statements (was 1: the 12D schema check); first research call 297 ms (calendar build; 295 ms in 12A).

**Synthetic scale** (disposable `qf_scale_synthetic`, since dropped; 1,000 companies and listings, 2,520,000 daily bars on genuine XNYS sessions, 40,000 events, 40,000 filings with FSDS submissions, 500 saved records through the real service, 918 MB):
statement counts did not change (earnings list 7 at 20 or 200 rows and at offset 39,800; filings 2; research 10 at 1, 3 and 10 horizons; saved list 2), i.e. **no N+1**; latency (median): earnings list 25 ms at 20 rows / 34 ms at 200, deep offset 44 ms; filings list 23 / 32 ms, deep offset 81 ms; ticker filter 8 ms;
research 6-7 ms regardless of horizons and of 2.5 M bars; saved list 3 ms (200 rows 7 ms), detail 2 ms, verify 8 ms. Offset pagination grows linearly with the offset (the worst case measured is 81 ms at offset 39,800 of 40,000). Pages on the production build: overview 25 ms, earnings list 38 ms (660 KB HTML for 50 rows),
research 19-23 ms, saved list 63 ms (22 API requests: 1 health + 1 list + 20 event lookups, bounded by the page size; a batch endpoint is POST-RC), all other pages under 31 ms. No performance target was invented; no regression was found, so no architectural change is proposed.

Frozen request budgets, measured on the real stack and at scale from the API access log: Overview 5, Earnings list 2, Earnings event 3, Filings list 2, Research finder 2, Research workspace 4, Saved record 2, verified 3 (Filing detail 2). Browser: 10-11 requests per page, 546-819 KB transferred, 461-463 KB of JavaScript, DOMContentLoaded 21-86 ms.

## 5. One production presentation change

`session-ladder.tsx`: a target session the calendar proves but for which no price is stored (`MISSING_TARGET_PRICE`) now carries the note "Price not stored for target session" in the session ladder, as the chart and table already say; before, it looked like a priced session.
This was found while recapturing the fixtures. It changes no data, status, label or contract.

## 6. Golden verification (validation database, no provider)

* Market: record `ed7a3a9b-c467-4e10-b537-0504b722ece3` verifies `MATCH` (row digest `93579f83...` unchanged); record `e880eaf9-0953-4458-b5b1-0d1d7a44affe` verifies `MANIFEST_CHANGED`, changed path `methodology.components[7][1]` only (row digest `700b5d2c...` unchanged).
  Anchor 2026-10-02; stock +1 0.0026572, +2 0.0218482; abnormal +1 -0.0040862, +2 0.0095698; PIT `INELIGIBLE_NO_AVAILABILITY_EVIDENCE` (REFUSED); +5 target 2026-10-09 is `MISSING_TARGET_PRICE` and was not shifted. The +5 / +20 bars were not fetched.
* Accounting NKE `0000320187-26-000037`: current 0.3500, comparative 0.5400, change -0.1900, display ratio -0.3518, `POSITIVE_DECREASED`.

## 7. Adjudication of the open register

| Item | Disposition | Evidence |
|---|---|---|
| Partial-bar coverage / storage | POST-RC (needs a migration); no new P0/P1 | absent bars are reported as `MISSING_TARGET_PRICE` / `MISSING_ANCHOR_PRICE`, never shifted |
| Remaining provider undecodable-200 raw loss (Massive splits / snapshots / events, SEC client) | ACCEPTED RC LIMITATION | the run fails loudly and is re-runnable; only the bytes of a malformed 200 are not retained |
| Soft 404 status codes | POST-RC | not-found states render HTTP 200 by design; smoke visits only existing identities |
| Calendar singleton race | ACCEPTED RC LIMITATION | measured: 16 simultaneous first requests built up to 11 calendars (about 1.3 s each under contention), all results identical, no error; once per process per UTC day |
| Stored EDGAR header `.json` extension | POST-RC | cosmetic |
| Unreachable partial branch | POST-RC | dead code |
| Saved-list batch enrichment | ACCEPTED RC LIMITATION | measured 22 requests / 63 ms per page at 500 records |
| Wheel-install migration metadata | ACCEPTED RC LIMITATION | documented: source install only |
| Connection-pooler `options` startup parameter | ACCEPTED RC LIMITATION | documented escape hatch |
| CLI statement timeout (60 s) | ACCEPTED RC LIMITATION | documented `QF_DB_STATEMENT_TIMEOUT_SECONDS` |
| Ctrl-C footer omission; manifest per-artifact disposition | POST-RC | operator ergonomics |
| Redaction edge cases (`password='quoted'`, JSON `"api_key"`, `client_secret=`) | **RESOLVED in 12F** | section 10 |
| Backend reason text "1-th / 2-th exchange session" (ordinals) | ACCEPTED RC LIMITATION | cosmetic, but the text is part of `result_fingerprint`; changing it changes saved-record fingerprints, which the goldens freeze |
| Ladder legend / alignment label "stored sessions" | **RESOLVED in 12F** | section 10 |
| Release smoke passes a page that renders an error state with HTTP 200 | **RESOLVED in 12E** | section 1 |
| Stale frontend fixtures | **RESOLVED in 12E** | section 2 |
| `braces` advisory, dev tooling | ACCEPTED (waiver) | section 3 |

## 8. Gates

Backend 3628 passed (2536 unit + 1092 integration), coverage 95.93 % (11,078 statements); ruff, format, mypy (278 files) clean; 39 migration / alembic tests pass; `alembic check`: no new upgrade operations; head `0014`; no migration diff.
Frontend 1511 unit tests (44 files), lint, typecheck, contract check, production build green; fixture e2e 227 passed (207 existing + 20 release-smoke controls), 0 failed.

## 9. Release recommendation

No P0 or P1 is open. The release candidate qualifies for a freeze (12F) subject to the prerequisites in the final report. Remaining constraints: private-network / authenticating-proxy deployment, one served schema revision, and the accepted limitations in section 7.

## 10. Step 12F: release-candidate freeze

Step 12E is committed as `00467823201afa1c11bf0605fcf6210709a10ff8` ("test: qualify release candidate"). 12F changes only presentation wording and the shared redaction helper; no backend status, API contract, methodology, financial value, saved-record fingerprint, migration or provider behaviour changed.

**Session wording (frontend, presentation only).** The session ladder is labelled "Exchange sessions" and its legend says the sessions come from the calendar, that a session whose price is not stored says so, and that a horizon the calendar cannot prove shows no date;
`ALIGNED` reads "Aligned to exchange sessions" and `NO_SESSION_DATA` "No qualifying exchange session". The four per-horizon cases stay apart, from existing statuses and wording helpers: a computed return (date), `MISSING_TARGET_PRICE` (date plus "Price not stored for target session"),
`INSUFFICIENT_FORWARD_SESSIONS` (no date, "Beyond the calendar's proven horizon"), and `MISSING_ANCHOR_PRICE` (every horizon names the missing anchor price). The saved-record evidence panel keeps "stored session dates": that digest really is of stored price-bar dates.

**Redaction (`ingestion/runs.py`, one shared helper).** The assignment rule now covers quoted values (`password='a b'`, `password="a b"`, escaped quotes), quoted keys and JSON bodies (`"api_key": "..."`, `"token":"..."`), prefixed names (`client_secret`, `x-api-key`, `refresh_token`, `DB_PASSWORD`), any case, unquoted values that contain commas or semicolons, and surrounding punctuation;
words that merely contain a name (`secretary`, `tokens`, `author`, `password_hint`) and a bare `?` are left alone, so diagnostic text survives. Input is capped to the first 5,000 characters before redaction, which also removes a pre-existing quadratic cost on pathological messages.
The review of the sinks found two real leaks and fixed both: (1) the CLI printed `error: ...` lines (and, for an unrecognised failure, the re-raised traceback) from the raw exception text while the footer and the row were redacted; every `error:` line now goes through `redact_text`, and the exception handed to the traceback carries the redacted text;
(2) the two "could not record / write ..." log lines used `logger.exception`, whose traceback repeats the raw message; they now log the class and the redacted text. Expected readiness-outage logs were already class-name only and are now pinned for the new forms.
Remaining redaction limits (documented, not a regression): a raw `/` in a DSN password cannot be told from the start of the path; a space-separated flag (`--password hunter2`) has no `=`/`:` to key on; an unexpected defect in the readiness probe keeps its traceback by design.

**Final gates (12F candidate).** Backend 3668 passed (2565 unit + 1103 integration), coverage 95.96 %; ruff, format, mypy (278 files) clean; 39 migration tests, `alembic check` clean, head `0014`, no migration change. Frontend 1514 unit tests, lint, typecheck, contract check, production build green; fixture e2e 227 passed (including the 20 release-smoke controls).
Real-stack smoke (validation database, production build): PASS with `--verify --db-fingerprint` (10 pages x 2 viewports, 20 tables fingerprinted, 26 GET from the script and 210 from the browser, no write); without `--verify` the verify page is not visited (9 pages). Negative: backend down, web down, schema mismatch (dev DB at 0013, read-only) and database unreachable each fail with the phase named; Redis stopped keeps readiness `ready`.
Request budgets unchanged (5 / 2 / 3 / 2 / 2 / 4 / 2 / 3). Audits: `uv audit` 0; `npm audit --omit=dev` 0; the full `npm audit` still reports the dev-only `braces` chain (5 packages, one root cause, waived in section 3), so **the full dependency tree is not clean**. Secret scan of all tracked files clean. Goldens unchanged (section 6). Provider requests 0 / 0 / 0.

**Deployment assumptions.** Private network or authenticating reverse proxy in front of the API and web (no authentication by design); one schema revision (`0014`) served at a time; PostgreSQL is the only serving dependency (Redis is reported, never gating); source/editable install for the API (a wheel install cannot establish its head and stays not ready); explicit production database login and CORS origins; Chrome available where the release smoke runs.

**Operator actions (not done by the freeze).** The development database `quantforge` is still at `0013`; an API pointed at it reports `503 schema mismatch` until the operator takes a backup and runs `alembic upgrade head` on it (runbook section 5). The real +5 (2026-10-09) and +20 (2026-10-30) market rechecks are independent follow-ups that spend provider requests and need a separate decision.

**Post-RC work.** Partial-bar coverage and storage (migration); soft-404 status codes; a saved-list batch endpoint; per-artifact manifest disposition; Ctrl-C footer; the dead partial branch; the `.json` header extension; undecodable-200 byte retention for Massive splits / snapshots / events and the SEC client; earnings-list HTML weight (about 12 KB per row); the backend ordinal text ("1-th"), which needs a methodology bump; a wheel-install head; cursor pagination; removing the `braces` waiver when a fix exists.

**Final review (one high-effort pass over the 12F diff and the whole candidate).** No P0, no P1, no unwaived RC-required P2. Found and fixed during 12F, each with a test: P2 the CLI printed `error:` lines and re-raised an unrecognised failure from the raw message (credential visible on stderr while the row was redacted); P2 two secondary-failure log lines carried a traceback that repeats the raw message;
P2 an unquoted value stopped at a comma (`password=ab,cd` leaked `cd`); P3 a bare `?` would have been mangled to `?[redacted]` in CLI error lines; P3 a pre-existing quadratic cost on pathological messages (input is now capped). Open P3: raw `/` in a DSN password, `--password x` flag form, best-effort `exc.args` redaction for exotic exception classes,
an unexpected readiness defect keeps its traceback by design. Checked and clean: no backend status / contract / methodology / value / fingerprint change (the only backend source files touched are `ingestion/runs.py` and `ingestion/cli.py`; no migration; both saved records' row digests unchanged and the immutability triggers present);
saved-record reproducibility (goldens); release-smoke false positives (20 controls incl. legitimate refusals); dependency and secret scans; no accidental source change (the 12F source diff is `session-ladder.tsx`, `research-wording.ts`, `runs.py`, `cli.py`).
