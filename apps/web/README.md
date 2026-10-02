# QuantForge Web

Next.js research terminal for the QuantForge platform. At this milestone it is
a single page: an Overview screen that reports whether the FastAPI backend
(`apps/api`) is reachable.

## What exists

- **Overview page** (`src/app/page.tsx`) — a server component that calls the
  backend's liveness endpoint once per render (no polling, no client-side
  refetch) and renders a "Connected" or "Unavailable" status. It reports
  process liveness only; the backend also exposes a readiness endpoint
  (`GET /api/v1/health/ready`, which separately reports PostgreSQL and Redis
  status), but this page does not call it yet.
- **Typed API client** (`src/lib/api/client.ts`, `src/lib/api/health.ts`,
  `src/types/api.ts`) — a `fetch` wrapper with a request timeout, that
  distinguishes a transport failure, an HTTP error, and a malformed response,
  and narrows the result against the backend's actual response shape rather
  than trusting it blindly.
- **Navigation shell** (`src/components/layout/{app-shell,sidebar,topbar}.tsx`,
  `src/lib/navigation.ts`) — six nav items are declared to settle the
  information architecture, but only Overview is `status: "available"` and
  linked; the rest are `"planned"` and rendered as disabled, not as dead
  links.

There is no authentication, no WebSocket/realtime connection, and no
data-fetching beyond the one health check above — those do not exist yet.

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
`NEXT_PUBLIC_API_BASE_URL` for the Overview page to report "Connected"; see
the repository root [`README.md`](../../README.md) for bringing it up. The
test suite does not need the backend running — it exercises the API client
and page against mocked responses.
