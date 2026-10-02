# QuantForge

Event-driven equity research and quantitative backtesting.

This repository has completed the **infrastructure foundation** (PostgreSQL,
Redis, SQLAlchemy, Alembic, health surface, web terminal reading real backend
state), the **financial domain schema** (the canonical point-in-time tables for
issuers, instruments, venues, price observations, benchmarks, earnings events
and provenance), and a working **market-data ingestion pipeline** for the
Massive provider: reference data, daily bars, and SPY benchmark levels, all
idempotent and fully provenance-tracked — live-verified against the real
Massive API (including an idempotent rerun), not just against stubs.
**Earnings persistence** is implemented and live-verified against Finnhub's
earnings-calendar endpoint — source-aware SHA-256 fingerprinting, idempotent
writes, and revision-preserving updates. Massive's own Benzinga earnings
endpoint remains entitlement-blocked (`403`) under the configured plan, which
is why Finnhub is the earnings-calendar provider.

On top of that fact layer sits a **point-in-time research engine**: event
alignment, EPS-surprise calculation, forward and abnormal returns, and a
unified earnings event-study (`quantforge.research`); a first **signal
family** — EPS-surprise classification, evaluation against realized outcomes,
and cross-event research analytics (`quantforge.signals`); and an **execution
eligibility gate** that decides, from a real exchange session clock
(`quantforge.market`, backed by `exchange_calendars`), whether a signal was
provably known strictly before a candidate session open
(`quantforge.backtest`, not yet committed — see [Current state](#current-state)).

There is still **no trading strategy, no order/position model, and no P&L** —
direction is never mapped to buy/sell, and an execution-eligibility result is
only a candidate *when* (a session open), never a fill, price or order. Data
arrives through the ingestion CLI and then only when a provider key is
configured.

```
apps/
├── api/     FastAPI service, SQLAlchemy/Alembic, Redis client
└── web/     Next.js research terminal
```

## Current state

| Layer | Status | Commit |
|---|---|---|
| Infrastructure + financial domain schema | Done | `b20e4b1` (pushed) |
| Market-data + earnings ingestion (Massive, Finnhub, SPY benchmark) | Done, live-verified | `b20e4b1` (pushed) |
| Research engine: event alignment, EPS surprise, returns, event study | Done | `0fc912f` (pushed) |
| Signal engine: EPS-surprise signal, evaluation, cross-event analytics | Done | `5599172` (pushed) |
| Market-session clock, execution eligibility, explicit price basis (`0006`) | Done, **uncommitted** | working tree |

The market-session clock (`quantforge/market/`) and the backtest layer
(`quantforge/backtest/`) and their tests are intentionally uncommitted as of
this writing, together with the explicit price-basis schema (migration `0006`).
Execution eligibility is decided against real session opens, and as-traded
prices can now be stored next to the split-adjusted research bars. What does not
exist is a numeric fill: no fill lookup, order, position or P&L, and no
corporate-action (split share-count) handling. See `NEXT_SESSION_PROMPT.md` / `HANDOFF.md`
(gitignored, local only) for exactly where to resume.

## Prerequisites

| Tool     | Version used | Notes                                                    |
| -------- | ------------ | -------------------------------------------------------- |
| Python   | 3.14         | managed with `uv`; pinned in `apps/api/.python-version`   |
| uv       | 0.12+        | dependency and environment manager                       |
| Node.js  | 24 LTS       | project-local install in `.tools/`                       |
| Docker   | any recent   | Docker Desktop or any Compose v2+ implementation         |

## Quick start

```bash
# 1. Infrastructure
cp .env.example .env
docker compose up -d
docker compose ps

# 2. Backend dependencies and schema
cd apps/api
uv sync --all-groups
uv run alembic upgrade head

# 3. Backend
uv run uvicorn quantforge.main:app --reload

# 4. Web (separate terminal)
cd apps/web
npm install
npm run dev
```

Then open <http://localhost:3000>. The Overview page reports live backend
connectivity.

## Financial domain

The backend owns eleven application tables. They split by what they claim
about time: *identity* (`company`, `security`, `exchange`, `listing`,
`benchmark`) is corrected in place and tracks edits; *facts* (`data_source`,
`security_identifier`, `daily_price_bar`, `benchmark_price_bar`,
`earnings_event`, `ingestion_run`) are immutable, because a correction must
arrive as a new source-attributed row rather than overwrite the original.

Conventions that matter when reading the schema:

- an issuer has no ticker — a symbol belongs to a `listing`, which is a
  security trading on a venue over a half-open date range `[valid_from, valid_to)`
- every price is `NUMERIC(20,6)` and reaches Python as `Decimal`; no floats
- instants are `TIMESTAMPTZ`, trading sessions and validity endpoints are `DATE`
- `observed_at` is the source's "as of" and may be NULL; `ingested_at` is ours
  and is what a point-in-time query must filter on
- all foreign keys are `ON DELETE RESTRICT`

Full details, including the `VARCHAR` + `CHECK` enum strategy and its
hand-written-migration caveat, are in [`apps/api/README.md`](apps/api/README.md).

## Market data ingestion

The `quantforge` console script (in `apps/api`, `uv sync --all-groups` first)
fetches US equity reference data and daily bars from the **Massive** provider.
It needs `QF_MASSIVE_API_KEY` and a migrated database:

```bash
cd apps/api
uv run quantforge ingestion reference AAPL MSFT   # resolve + store listings
uv run quantforge ingestion daily AAPL --start 2026-01-01 --end 2026-01-31
uv run quantforge ingestion daily AAPL --start 2026-01-01 --end 2026-01-31 \
  --exchange XNAS                                 # disambiguate a multi-venue ticker
uv run quantforge ingestion daily AAPL --start 2026-01-01 --end 2026-01-31 \
  --price-basis as-traded                         # adjusted=false rows, stored beside the research rows
uv run quantforge ingestion benchmark SPY --start 2026-01-01 --end 2026-01-31 \
  --name "SPY (ETF proxy for the S&P 500)"         # benchmark levels, explicit identity required
uv run quantforge ingestion runs                  # recent runs, newest first
uv run quantforge ingestion runs --limit 20 --dataset daily-bars
```

Writing data is deliberately conservative: reference resolution matches on CIK
and FIGI, never on a name; daily bars are idempotent per
`(listing, session, source)`, and a provider revising an already-stored bar is
a conflict that fails the run. Every fetch is recorded in `ingestion_run` and
the provider's raw bytes are stored byte-for-byte, content-addressed by their
own SHA-256, under `data/raw/`. Ingestion commands fail fast without an API
key, and the key is never written to disk or to the database.

Massive's daily aggregates are requested with an explicit price basis. The
default and research basis, `--price-basis split-adjusted`, sends
`adjusted=true`, which means **split-adjusted, not dividend-adjusted** — `close`
already has stock splits compensated for, but cash dividends are excluded, so
research returns computed from it are price returns, not total returns.
`--price-basis as-traded` sends `adjusted=false` and stores the as-printed
prices as separate rows (the candidate execution/accounting basis). The one benchmark
currently ingested, **SPY, is an ETF proxy for the S&P 500, not the index
itself** — the real index dataset is unavailable under the configured Massive
plan. See [`apps/api/README.md`](apps/api/README.md#ingestion) for the full
detail.

## Research, signals, and backtest layers

Three packages in `apps/api/src/quantforge/`, each strictly downstream of the
last, with no outcome data ever allowed to leak backward:

```
research/  earnings observation -> event alignment -> EPS surprise
           -> forward/benchmark/abnormal returns -> EventStudyResult
signals/   historically knowable facts -> EpsSurpriseSignal
           -> SignalEvaluation (against realized outcomes)
           -> cross-event SignalCohortSummary analytics
market/    exchange_calendars behind SessionCalendar -> MarketSession
           (exact UTC session open/close for XNYS/XNAS/ARCX)
backtest/  EpsSurpriseSignal + SessionCalendar -> ExecutionEligibility
           (whether/when a signal has a defensible candidate execution
           session -- never what trade to place, never a price)
```

Load-bearing invariants, enforced structurally (import graph, not just
convention) and by regression tests:

- **DESCRIPTIVE vs. POINT_IN_TIME** research modes are never pooled; a
  `POINT_IN_TIME` observation with no `observed_at` evidence is never treated
  as tradable, regardless of how the event's timing is labelled.
- **Signal generation and execution eligibility never see an outcome or a
  price.** `signals/eps_surprise.py` and `backtest/` neither import nor, when
  imported, load the return / benchmark / abnormal-return / event-study /
  evaluation / analytics modules. This is checked two ways: an AST scan of
  `backtest/` (direct and relative imports, dynamic-import calls) and a
  clean-interpreter `sys.modules` test (`tests/test_module_isolation.py`).
  `quantforge.research` and `quantforge.signals` export lazily (PEP 562) so
  their `__init__` files do not undo that. Execution eligibility is strict:
  for `BEFORE_MARKET`, `observed_at < session open` gives a
  `SAME_SESSION_OPEN` candidate; for `AFTER_MARKET`,
  `observed_at < next session open` (the calendar's next session, not day
  arithmetic) gives `NEXT_SESSION_OPEN`. Equality refuses. A `DESCRIPTIVE`
  signal, a NULL `observed_at`, `DURING_MARKET`, `UNKNOWN` timing and any
  unsupported MIC are all refused. The timing label only picks the session;
  the signal's real `observed_at` (never `ingested_at`) decides.
- **Prices are explicitly split-adjusted for research, never total-return, and
  SPY is an ETF proxy.** Each price row carries a required `price_basis`.
  `SPLIT_ADJUSTED` (Massive `adjusted=true`: splits only, dividends excluded) is
  what every research query selects explicitly; `AS_TRADED` (`adjusted=false`)
  is stored separately for a future execution/accounting path and is never read
  by `research/`. The same session may hold both (migration `0006`, both
  `daily_price_bar` and `benchmark_price_bar`). `AS_TRADED` across a stock split
  still needs a share-count adjustment that QuantForge does not implement, so no
  numeric fill, position or P&L exists yet.

## Infrastructure

Only two backing services are defined, both bound to loopback so nothing is
exposed to the network.

| Service    | Image       | Port    | Volume                 |
| ---------- | ----------- | ------- | ---------------------- |
| `postgres` | `postgres:18` | `5432` | `quantforge_postgres_data` |
| `redis`    | `redis:8`     | `6379` | `quantforge_redis_data`    |

```bash
docker compose up -d     # start (or resume) both services
docker compose ps        # status, health and published ports
docker compose logs -f   # follow logs
docker compose down      # stop and remove containers, KEEP the data volumes
```

> ### `docker compose down -v` deletes your local data
>
> ```bash
>
> docker compose down -v
> ```
>
> The `-v` flag removes the named volumes, which **permanently deletes the
> PostgreSQL cluster and all Redis keys** in this project. Every table, row and
> migration-applied schema is lost, and the next `docker compose up -d` starts
> from a brand-new, empty database. Use it only when you intend a clean slate.

Credentials come from the environment. `.env` is git-ignored; only
`.env.example` is committed.

## Migrations

Run from `apps/api`:

```bash
uv run alembic upgrade head            # apply everything
uv run alembic current                 # which revision is applied
uv run alembic current --verbose       # ...with its description
uv run alembic history                 # full lineage
uv run alembic downgrade -1            # step back one revision
uv run alembic check                   # fail if models have drifted from the DB
uv run alembic revision --autogenerate -m "describe the change"
```

`alembic.ini` deliberately contains **no database URL**. `alembic/env.py` reads
`QF_DATABASE_URL` from the application `Settings`, so migrations always target
the same database as the API and no credential is ever committed. Autogenerate
compares against `Base.metadata`; `alembic check` fails if the models have
drifted from the applied schema, and the test suite runs it as a gate. Five
revisions follow the empty `0001`: the financial domain (`0002`), ingestion
runs / issuer CIK (`0003`), earnings ingestion persistence (`0004`), and the
earnings `event_date`/`event_timestamp` correction (`0005`), and explicit price
basis on both bar tables (`0006`, current head). The research, signal, and
backtest layers are all computed, not persisted.

## Health endpoints

| Endpoint                | Checks              | On failure            |
| ----------------------- | ------------------- | --------------------- |
| `GET /health`           | process liveness    | n/a                   |
| `GET /api/v1/health`    | process liveness    | n/a                   |
| `GET /api/v1/health/ready` | PostgreSQL + Redis | `503`, names the failed dependency |

The liveness endpoints never touch a datastore, so they stay `200` even when
PostgreSQL and Redis are both stopped — use them for container and orchestrator
probes. Readiness actively probes both dependencies and returns `503` with a
typed body if either is unavailable:

```json
{
  "status": "not_ready",
  "service": "quantforge-api",
  "dependencies": { "database": "ok", "redis": "unavailable" },
  "detail": { "redis": "ConnectionError" }
}
```

`detail` reports only the exception class name. Passwords, DSNs, stack traces and
driver messages are never returned.

## Quality gates

```bash
# Backend
cd apps/api
uv run pytest -q --cov=quantforge --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy

# Frontend
cd apps/web
npm run lint
npm run typecheck
npm run test
npm run build
```

The backend test suite does **not** require Docker: dependency probes are driven
through injected stubs, and genuine-failure tests target an unreachable loopback
port.
