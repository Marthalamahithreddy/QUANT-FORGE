# QuantForge Web

Next.js research workspace for the QuantForge platform (read-only; it displays what the FastAPI backend in
`apps/api` serves).

## What exists

Frontend V2 is being built incrementally (design: `docs/frontend-v2-design.md`). Step 11.1B delivered the
foundations; the data pages are migrated in later steps.

- **Routes** — Overview `/`, Earnings `/earnings` and `/earnings/[id]`, Filings `/filings` and
  `/filings/[accession]`, Research `/research` (principles only) and Saved research `/research/saved` (a
  placeholder: nothing can be saved or listed from the interface yet).
- **Shell** (`src/components/layout`) — a 12.5rem navigation rail (Overview / Evidence: Earnings, Filings /
  Research: Research, Saved research), a 44px header strip (breadcrumbs, theme control, API status) and the
  main region. Below 1024px the rail becomes an accessible drawer. Companies and Backtests are not shown.
- **Tokens and theme** (`src/app/globals.css`) — semantic tokens for dark (default) and light, with the pre-V2
  names kept as aliases. The theme (`system` / `light` / `dark`) is the server-readable cookie `qf-theme`; the
  server renders the attribute, so there is no flash. No `localStorage`. Direction tint is a `qf-tint=on` cookie
  (default off; no UI yet).
- **Primitives** — layout (`PageContainer` 64/96/120rem, `PageHeader`, `Section`, `Frame`, `Callout`), financial
  (`FinancialValue`, `PercentValue`: exact strings, never a JS number), the ten-state domain vocabulary
  (`DomainState`, `RefusalNotice`, `StatusBadge`, `ReproducibilityBadge`), and `EmptyState`, `ErrorState`
  (never prints a service address) and `Skeleton`.
- **Typed API client** (`src/lib/api`, `src/types`) — unchanged in 11.1B: a `fetch` wrapper with a timeout that
  distinguishes a transport failure, an HTTP error and a malformed response, with strict parsers. User-visible
  error text uses `ApiError.publicMessage` (no address).

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
```

The backend (`apps/api`) must be running and reachable at
`NEXT_PUBLIC_API_BASE_URL` for the pages to report "Connected"; see
the repository root [`README.md`](../../README.md) for bringing it up. The
test suite does not need the backend running — it exercises the API client
and page against mocked responses.
