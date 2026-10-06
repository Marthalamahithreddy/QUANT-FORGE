# QuantForge Web

Next.js research workspace for the QuantForge platform (read-only; it displays what the FastAPI backend in
`apps/api` serves).

## What exists

Frontend V2 is being built incrementally (design: `docs/frontend-v2-design.md`). 11.1B delivered the foundations;
11.1C delivered the API contract layer and migrated Earnings and Filings.

- **Routes** — Overview `/`, Earnings `/earnings` and `/earnings/[id]`, Filings `/filings` and
  `/filings/[accession]`, Research `/research` (principles only), and two placeholders that exist only so the
  approved navigation and the "Open research workspace" action never lead to a dead link:
  `/research/saved` and `/research/events/[eventId]` (each says the feature is not part of the interface yet,
  shows nothing computed, and makes no API request).
- **Shell** (`src/components/layout`) — rendered ONCE by the root layout, so navigation never remounts it. A
  12.5rem rail, a 44px header strip (current section, theme control, API status) and the main region. The status
  chip streams behind `Suspense` ("Checking API" first), so a slow or failed health read never delays the page or
  blanks the chrome. It reflects the API when the document was loaded (stated in its tooltip); each page reports
  its own data failures. Below 1024px the rail becomes an accessible drawer.
- **Tokens and theme** — semantic tokens for dark (default) and light; the theme is the server-readable cookie
  `qf-theme` (no flash, no `localStorage`).
- **Data display** — `DataTable` (a server component: semantic table, sticky identity column and header,
  priority-based column hiding, labelled scroll region; no client sorting or filtering), `Pagination`
  (limit/offset/total, canonical URLs), `FilterBar` (GET form, only server-supported filters), `KeyValueList`,
  `ListEmpty`, `FilterErrors`, `ApiFailureState`.
- **Financial and state primitives** — `FinancialValue`, `PercentValue` (exact strings, never a JS number) and the
  ten-state domain vocabulary; backend statuses map onto it through exhaustive tables in `src/lib/state-map.ts`.

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
- **Timeouts** — one class today: `READ` (4 s) for health, readiness, list pages, one event and one filing EPS.
  The computed endpoints (research observation, record verification) will get a longer class with their clients.
- **No `server-only` package** — the import-graph test gives the same guarantee without a dependency.

There is no authentication, no realtime connection and no browser-side data fetching: every API read happens
on the server.

## Configuration

| Variable                   | Default                 |
| --------------------------- | ------------------------ |
| `NEXT_PUBLIC_API_BASE_URL`  | `http://localhost:8000` |

Read once, validated as an absolute `http(s)` URL, in `src/lib/config/env.ts`.
Copy `.env.example` to `.env` to override it for local development.

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

npm run contract:export   # refresh contract/openapi.json and the generated types from the backend
npm run contract:check    # fail if the backend schema, the snapshot and the types disagree
```

The backend (`apps/api`) must be running and reachable at
`NEXT_PUBLIC_API_BASE_URL` for the pages to report "Connected"; see
the repository root [`README.md`](../../README.md) for bringing it up. The
test suite does not need the backend running — it exercises the API client
and page against mocked responses.
