# QuantForge Web

Next.js research workspace for the QuantForge platform (read-only; it displays what the FastAPI backend in
`apps/api` serves).

## What exists

Frontend V2 (design: `docs/frontend-v2-design.md`) was built in slices: 11.1B the foundations; 11.1C the API
contract layer and the Earnings and Filings surfaces; 11.1D the research workspace; 11.1E the read-only Saved
Research viewer; 11.1F the convergence slice (Overview, cross-surface consistency, browser end-to-end tests,
accessibility and responsive convergence, request budgets, dead-code removal).

- **Overview** (`/`) — a launchpad, not a dashboard. "Start research" and four independent regions: latest earnings
  events, latest filings, recent saved research, and the API's own health and readiness. Each region is one bounded
  list read (five rows) in its own `Suspense` boundary, so a slow, empty or failed source never blanks another; none
  enriches a row, reads a detail endpoint or computes anything. There is no count, score or aggregate of its own.
- **Routes** — Overview `/`, Companies `/companies` and `/companies/[cik]` (company search and a revenue, net income
  and diluted EPS history with filing evidence; what it shows and its limits are in the root
  [README, "Companies"](../../README.md#companies); running it against the demonstration data is in
  [README, "View the demo data"](../../README.md#view-the-demo-data)),
  Earnings `/earnings` and `/earnings/[id]`, Filings `/filings` and
  `/filings/[accession]`, Research `/research` (an event finder) and the research workspace
  `/research/events/[eventId]` (11.1D), Saved Research `/research/saved` and `/research/saved/[recordId]` (11.1E).
- **Shell** (`src/components/layout`) — rendered ONCE by the root layout, so navigation never remounts it. A
  12.5rem rail, a 44px header strip (current section, theme control, API status) and the main region. The status
  chip streams behind `Suspense` ("Checking API" first), so a slow or failed health read never delays the page or
  blanks the chrome. It reflects the API when the document was loaded (stated in its tooltip); each page reports
  its own data failures. Below 1024px the rail becomes an accessible drawer.
- **Links never prefetch the API** — every in-app link is an `AppLink` (`prefetch={false}`) except the primary
  navigation. In production a viewport-prefetched dynamic page is RENDERED on the server and reads the API, so a
  list of N rows made N extra reads, and a prefetched `?verify=1` address could start a verification. A structural
  test pins that nothing else imports `next/link`; `e2e/request-budget.spec.ts` pins the request count per surface.
- **Tokens and theme** — semantic tokens for dark (default) and light; the theme is the server-readable cookie
  `qf-theme` (no flash, no `localStorage`).
- **Data display** — `DataTable` (a server component: semantic table, sticky identity column and header,
  priority-based column hiding, labelled scroll region; no client sorting or filtering), `Pagination`
  (limit/offset/total, canonical URLs), `FilterBar` (GET form, only server-supported filters), `KeyValueList`,
  `ListEmpty`, `FilterErrors`, `ApiFailureState`.
- **Financial and state primitives** — `FinancialValue`, `PercentValue` (exact strings, never a JS number) and the
  ten-state domain vocabulary; backend statuses map onto it through exhaustive tables in `src/lib/state-map.ts`.

## Research workspace (`/research/events/[eventId]`)

One stored earnings event is the identity. The page paints its header, context bar and configuration form at
once; the result region streams behind a `Suspense` keyed by the study's canonical address (a client navigation
that changes only the query string replays the skeleton and never leaves an old study under new settings).

- **URL contract** (names are the API's): `horizons` (comma list, 1 to 252, at most 10, no spaces, no duplicates;
  written ascending; the default `1,5,20` is omitted), `benchmark_id` (UUID, lower-cased), `filing_accession`
  with BOTH `eps_period_class` and `eps_kind`, and `bpage` (which page of the benchmark index the selector shows;
  selector position only, not part of the study). Defaults and empty values are omitted; a valid but non-canonical
  address is redirected before any read; an invalid one shows a local error and makes NO observation request.
  Selectors without a filing are dropped, never sent.
- **Nothing is inferred.** No benchmark is chosen, recommended or defaulted (the selector starts at "None"),
  nothing is remembered between events, and no filing is ever linked unless the reader supplies its accession.
  A filing picker was considered and rejected: the filing index lists only FSDS-backed filings (no 8-Ks, so no
  Item 2.02 disclosure evidence), so a picker would show an incomplete candidate set and imply a relationship.
  The form offers an accession field and a plain "Filings for this registrant" lookup link that claims nothing.
- **Benchmarks** come from `GET /benchmarks`, one page of 200 at a time (it is paginated: the first page is not the
  universe, there is no search over a partial page). Active first, inactive in their own group and labelled so.
  If the index cannot be read, the study without a benchmark is unaffected.
- **One request.** One observation carries both modes, so the page asks for it once (10 s class). The benchmark
  index is read once per request (React `cache`) and only NAMES a benchmark already selected, in its own boundary.
- **Descriptive and point-in-time are separate frames** (label, solid versus dashed left rule, mode hue, state
  badge). A point-in-time refusal is a callout inside its own frame: it never dims or reorders the descriptive study.
- **Nothing is recomputed in React.** Returns, benchmark returns and the abnormal return are the backend's exact
  strings (the abnormal return is read, never subtracted). The event-study chart is server-rendered SVG with an
  authoritative table beside it. Only `src/lib/chart/geometry.ts` turns a string into a number, to place a mark;
  its output is never shown as a figure, a label of data, or an input to refusal or point-in-time logic
  (`tests/research-structure.test.ts`).
- **Refusals** are listed once in a ledger grouped by scope (code, mark, horizon, reason verbatim); sections point
  at it. Codes must belong to their scope's vocabulary (an unknown code is contract drift, not a new state).
- **Provenance** opens in a native-dialog drawer (`EvidenceDrawer`, the only new client component). A live study
  has no reproducibility level: that belongs to saved baselines.

Real observation responses are pinned in `tests/contract/observation.json`, captured by
`scripts/capture_observation_fixtures.py` (see its header; it uses the backend's own integration scenario builders
and changes no backend file).

## Saved research (`/research/saved`, `/research/saved/[recordId]`)

A read-only viewer of immutable research records that already exist. There is no save, edit, delete, rename,
ownership or authentication anywhere in the interface (a future save workflow is to be designed together with
authentication and authorization). Four things are kept apart on screen and in code: the **stored baseline**, its
**reproducibility level**, an optional **verification** (a re-run against today's stored evidence), and a **live**
Research study.

- **List** — `GET /research/records?limit=20&offset=…`, page in the URL (`?page=`, defaults omitted, no `limit`
  parameter), a `DataTable` of stored fields (record id, saved time in UTC, reproducibility level with its gaps,
  stored request, manifest fingerprint, result fingerprint; fingerprints are shortened for display and whole in the
  document, and they are different identities). The one extra column is the CURRENT event record (company, ticker,
  date), looked up once per distinct event on the page, labelled as current, and tolerant of a 404.
- **Record** — a `STORED BASELINE` banner, identity (full, copyable fingerprints), the stored request, the
  reproducibility section (COMPLETE/PARTIAL with each gap's sentence; PARTIAL is a statement about the evidence trail,
  never styled as a failure), the stored result rendered with the same study components as the live workspace (via
  pure adapters in `src/lib/stored-result.ts`; never through the live endpoint, nothing current merged in), the
  consumed evidence (shared `EvidenceDrawer`, stored identifiers), then verification and a re-run link.
- **Verification** is explicit: `?verify=1` runs ONE `GET …/verify` (10 s computed class) inside its own keyed
  `Suspense`, so the baseline never waits for it. The status (MATCH, EVIDENCE_INCOMPLETE, MANIFEST_CHANGED,
  RESULT_CHANGED) is the API's and is only ever read; changed paths come from the API and are grouped for reading;
  no value is diffed in React. 404, 409 (the record's event is no longer stored), 500 (a stored record failed its own
  integrity validation), 503, network and contract failures are failures, never statuses.
- **Re-run live** is a link built from the stored request (event, horizons, benchmark, filing accession and its two
  selectors only). It issues nothing; the workspace it opens is a new live study.
- Real record responses are pinned in `tests/contract/records.json`, captured by
  `scripts/capture_record_fixtures.py` (the backend's own scenario builders and save service over PostgreSQL; no
  backend file changed). The stored manifest and result are open objects in the OpenAPI schema, so the decoders in
  `src/lib/api/decoders/records.ts` and those fixtures are what pin them.

## API contract layer

All API access is server-side (`src/lib/api`); the browser never calls the API, and a structural test proves no
client component can reach the client, the decoders or the config.

- **Snapshot** — `contract/openapi.json`, produced by the backend's own `create_app().openapi()` through
  `scripts/export_openapi.py` (run in the backend environment with every `QF_*` variable and any `.env` ignored,
  keys sorted, no timestamp, path or hostname). The same schema always yields the same bytes.
- **Generated types** — `src/types/generated/api.d.ts`, from the snapshot with `openapi-typescript` (dev
  dependency). Types only: compile-time assistance, never runtime validation, and never imported at runtime.
- **Refresh** — `npm run contract:export` (needs `uv` and the backend environment) rewrites both files.
- **Drift check** — `npm run contract:check` regenerates from the live backend and fails if the committed
  snapshot or generated types differ (it needs the backend toolchain, so it is not part of `npm run test`).
  `tests/openapi-contract.test.ts` runs in every test run and proves the generated types equal what the snapshot
  produces, that every runtime enum list, state map and wording table equals the snapshot, and that decoders
  require every key the snapshot marks required.
- **Decoders** (`src/lib/api/decode.ts`, `decoders/`) — hand-written and strict: every documented key must be
  present (`null` is a value, absent is drift), decimals are exact strings (a JSON number, padding or exponent is
  rejected), an unknown enum member is rejected, extra keys are ignored. A failure names the path and what was
  expected, never the received value.
- **One result type** — `ApiResult<T>`: `ok`, `not_found` (a read of one resource only), `conflict`, `rejected`
  (422), `unavailable` (503), `server_error`, `unreachable` (timeout or no response) and `contract`. A domain
  status inside a 200 (REFUSED, AMBIGUOUS, MISSING, PARTIAL, NOT_REQUESTED) is `ok` data, never an error.
  Failure wording lives in `src/lib/api/messages.ts`: no address, host, stack, SQL or raw exception.
- **Timeouts** — `READ` (4 s) for health, readiness, list pages, benchmarks, one event and one filing EPS;
  `OBSERVATION` (10 s) for the research observation and the record verification only.
- **No `server-only` package** — the import-graph test gives the same guarantee without a dependency.

There is no authentication, no realtime connection and no browser-side data fetching: every API read happens
on the server.

## Configuration

| Variable                   | Default                 |
| --------------------------- | ------------------------ |
| `NEXT_PUBLIC_API_BASE_URL`  | `http://localhost:8000` |

Read once, validated as an absolute `http(s)` URL, in `src/lib/config/env.ts`.
Copy `.env.example` to `.env` to override it for local development.

## Browser end-to-end tests (`e2e/`, Playwright)

Two separate things, never conflated:

- **Fixture-backed E2E** (`npm run e2e`) — the PRODUCTION build (`next build` + `next start`, into its own
  `.next-e2e` directory, because the API address is fixed into a build) against `e2e/fixture-api.mjs`, a stand-in
  API that serves the real backend responses captured under `tests/contract/` byte for byte. Nothing is simulated or
  hand-edited; a request no capture answers is refused with 501. Test-only control routes (`/__e2e/*`) inject faults
  (a 503, a contract drift, a delay) and read the request log. It covers the journeys: Earnings to Research,
  PIT refusal, benchmark failure, filing and accounting, Saved Research, verification (MATCH, EVIDENCE_INCOMPLETE,
  MANIFEST_CHANGED, RESULT_CHANGED, a 409), Re-run live, theme, mobile navigation, error safety, keyboard-only
  journeys, loading, axe in real Chrome (both themes, narrow viewport, drawers) and a responsive matrix
  (14 routes x 1440/1024/390/320 x dark/light). `e2e/request-budget.spec.ts` pins the API requests per surface.
- **Live seeded-backend smoke** (`npm run e2e:live`) — the normal build against a real API on `localhost:8000` and
  its seeded database. Small by design: it claims nothing about states the database does not hold (it has no
  filings, no saved records and no price bars).

System Chrome is used (`channel: "chrome"`), so no browser is downloaded. Refresh the saved-record captures with
`scripts/capture_record_fixtures.py` (its docstring has the command); never edit a capture by hand.

## Commands

```bash
npm install

npm run dev         # start the dev server at http://localhost:3000
npm run build        # production build
npm run start         # serve a production build

npm run lint          # eslint
npm run typecheck     # next's route-type generation, then tsc --noEmit
npm run test          # vitest, single run
npm run test:watch    # vitest, watch mode

npm run e2e               # fixture-backed browser tests: builds into .next-e2e, then runs Playwright
npm run e2e:run           # the same tests without rebuilding
npm run e2e:live          # live seeded-backend smoke (API on :8000; builds the normal .next)

npm run contract:export   # refresh contract/openapi.json and the generated types from the backend
npm run contract:check    # fail if the backend schema, the snapshot and the types disagree
```

The backend (`apps/api`) must be running and reachable at
`NEXT_PUBLIC_API_BASE_URL` for the pages to report "Connected"; see
the repository root [`README.md`](../../README.md) for bringing it up. The
test suite does not need the backend running — it exercises the API client
and page against mocked responses.
