# QuantForge API

FastAPI backend for QuantForge: a read-only equity research API and ingestion CLI over
provenance-tracked market data, SEC filings and financial facts, including an earnings
event-study research engine. It has no order, fill, position or P&L model.

This package has completed the **infrastructure foundation** (configuration,
PostgreSQL and Redis connectivity, migrations, health surface), the
**financial domain schema** (the canonical point-in-time tables for issuers,
instruments, venues, price observations, benchmarks, earnings events and
provenance, plus typed Pydantic schemas and explicit repositories), and a
working **market-data ingestion pipeline** for the Massive provider: reference
data and daily bars, both idempotent, provider-attributed to the raw response,
and recorded in a run table.

There is still no backtesting or analytics logic, and no data exists until
ingestion runs.

## Layout

This tree shows the layout at the early steps; `src/quantforge/` now also holds `financials/`, `research/`, `signals/`,
`backtest/`, `market/`, `identity/`, `observation/`, `reproducibility/` and `research_records/`. See the root
[README](../../README.md#architecture) and [`docs/implementation-notes.md`](../../docs/implementation-notes.md).

```
src/quantforge/
├── main.py            # application factory + connection lifecycle
├── api/
│   ├── router.py      # aggregates the versioned surface
│   └── v1/
│       ├── router.py
│       ├── health.py  # liveness probe (no datastore access)
│       └── ready.py   # readiness probe (PostgreSQL + Redis)
├── cache/
│   └── redis.py       # lazy Redis client, PING probe, explicit close
├── core/
│   ├── config.py      # Pydantic Settings (QF_* environment variables)
│   └── dependencies.py # shared dependency-status vocabulary
├── db/
│   ├── base.py        # DeclarativeBase + deterministic constraint naming
│   └── session.py     # lazy Engine / sessionmaker / connectivity probe
├── models/            # SQLAlchemy 2.0 declarative domain model
│   ├── base.py        # UUID/timestamp mixins, NUMERIC and timestamptz factories
│   ├── enums.py       # closed vocabularies as VARCHAR + CHECK
│   ├── provenance.py  # data_source
│   ├── company.py     # company
│   ├── security.py    # security, exchange, listing, security_identifier
│   ├── market.py      # daily_price_bar
│   ├── benchmark.py   # benchmark, benchmark_price_bar
│   └── earnings.py    # earnings_event
├── schemas/           # Pydantic v2 boundary schemas (create/read per model)
├── repositories/      # explicit Session-bound repositories, one per aggregate
├── ingestion/         # CLI + service: reference, daily bars, raw store, runs
└── providers/         # provider boundary; Massive client, DTOs, mapper, pacing

alembic/               # migration environment
├── env.py             # URL from Settings, target = Base.metadata
└── versions/
    ├── 0001_baseline.py
    ├── 0002_financial_domain.py
    ├── 0003_ingestion_runs_and_issuer_cik.py
    └── 0004_earnings_ingestion.py
```

## Domain schema

Eleven tables, grouped by what they assert about time, plus the provenance row
that records who delivered them.

**Identity and reference data** — mutable, corrected in place, so each carries
`created_at` and `updated_at`:

| Table                 | Holds                                                        |
| --------------------- | ------------------------------------------------------------ |
| `company`             | the legal issuer. Deliberately has **no ticker**             |
| `security`            | one instrument class issued by that company                  |
| `exchange`            | a trading venue, keyed by ISO 10383 MIC                      |
| `listing`             | a security trading under a ticker on a venue over a date range |
| `benchmark`           | a reference index                                            |

**Facts about the world** — immutable, with no `updated_at`, because a correction
must arrive as a new row rather than overwrite the original:

| Table                   | Holds                                                |
| ----------------------- | ---------------------------------------------------- |
| `data_source`           | a registered provider and what kind it is            |
| `security_identifier`   | ISIN / CUSIP / SEDOL / FIGI and other external codes |
| `daily_price_bar`       | one source's OHLCV for one listing on one session     |
| `benchmark_price_bar`   | one source's close for one index on one session       |
| `earnings_event`        | reported figures and the event timestamp              |
| `ingestion_run`         | one documented provider fetch: what, when, how it ended |

### Conventions worth knowing before you write a query

- **UUID surrogate keys**, generated in Python (no `gen_random_uuid()`), so
  `id` is stable across a rename, a ticker change and a re-listing.
- **Instants are `TIMESTAMPTZ`**; **sessions and validity endpoints are `DATE`**.
  A trading session is a calendar fact and must never carry a timezone.
- **Every price is `NUMERIC(20,6)`** and reaches Python as `Decimal`. There is no
  `float` anywhere in the price path.
- **Listing validity is half-open `[valid_from, valid_to)`**, and
  `valid_to IS NULL` means "still current". Two consecutive periods may share an
  endpoint, which is what makes a clean ticker handover expressible.
- **`observed_at` is the source's "as of" and may be NULL**; `ingested_at` is
  ours and is always present. Point-in-time queries must use `ingested_at`.
- **All foreign keys are `ON DELETE RESTRICT`.** Reference data is never removed
  out from under the price history that depends on it.

### Enums are `VARCHAR` + `CHECK`, not PostgreSQL `ENUM`

A native `ENUM` can never drop or rename a value, and adding one needs a
hand-written migration that Alembic's autogenerate will not produce. Every
vocabulary is therefore a `VARCHAR` with a named `CHECK` whose literal list is
generated from the Python `StrEnum`, so the database can never drift from the
application.

**The corollary is a manual step:** Alembic does not diff `CHECK` constraint
bodies, so *adding an enum member* requires a hand-written migration that drops
and recreates the constraint. `alembic check` will stay silent about it.

```python
# in a new migration, after adding the member to the StrEnum
op.drop_constraint("ck_security_security_type_enum_values", "security", type_="check")
op.create_check_constraint(
    "ck_security_security_type_enum_values",
    "security",
    "security_type IN ('EQUITY', 'PREFERRED_EQUITY', 'DEPOSITORY_RECEIPT', "
    "'ETF', 'MUTUAL_FUND', 'BOND', 'WARRANT', 'OTHER')",
)
```

### Other deliberate omissions

- **No temporal-overlap exclusion** on listings. `UNIQUE` plus two partial
  indexes covers the current row; a general `daterange`/`btree_gist` exclusion
  needs an extension and is deferred.
- **No uniqueness on `(company, fiscal_year, fiscal_quarter)`** for earnings.
  Restatements and dual-class filings are real, and a unique constraint would
  silently drop them.
- **No derived analytics.** Surprise, tradability, returns and abnormal returns
  are computed later, from a stated source and a stated adjustment, rather than
  frozen into the table at write time.

## Ingestion

Market data is fetched from **Massive** by the `quantforge` console script and
stored with two intact guarantees: nothing is written unless the provider
actually said it, and no write is ever silently overwritten.

```bash
# Resolve reference data for one or more symbols (venue, issuer, instrument).
uv run quantforge ingestion reference AAPL MSFT
# Fetch daily bars for an already-ingested symbol. The listing must exist
# first, because a bar's session date depends on the venue's timezone.
uv run quantforge ingestion daily AAPL --start 2026-01-01 --end 2026-01-31
# --exchange (MIC) disambiguates a symbol currently listed on more than one venue.
uv run quantforge ingestion daily AAPL --start 2026-01-01 --end 2026-01-31 --exchange XNAS
# Fetch daily levels for an explicit benchmark instrument (see "Benchmark" below).
uv run quantforge ingestion benchmark SPY --start 2026-01-01 --end 2026-01-31 \
  --name "SPY (ETF proxy for the S&P 500)"
# List recent runs, newest first. --limit caps the rows (default 10);
# --dataset filters to one kind, e.g. reference or daily-bars.
uv run quantforge ingestion runs
uv run quantforge ingestion runs --limit 20 --dataset daily-bars
```

All commands need `QF_MASSIVE_API_KEY` (ingestion refuses to start without it)
and a migrated database.

**Reference.** One symbol becomes four kinds of row: an `exchange` (the venue,
resolved by MIC), a `company` issuer (matched on CIK only — a name is never
enough), an instrument (`security`, matched on FIGI), and a `listing` bound to
the current venue. Resolution is idempotent: re-running the same symbols
matches existing rows and writes nothing new. A ticker the provider does not
know is a legitimate answer, counted and reported, not an error — one symbol's
failure never abandons the rest of the batch.

**Daily bars.** The bars endpoint carries no exchange code, so the venue must
already be stored (reference first) to convert a bar's UTC instant into a
session date on the right calendar. Ingestion is idempotent per
`(listing, session, source)`; a re-run skips the bars it already holds. A
provider that *revises* a stored bar is a conflict — `ProviderDataConflict` —
and fails the run rather than being swallowed.

There is no partial bar ingestion. A bar that fails validation (malformed row,
non-finite or non-positive price, `low > high`, open/close outside
`[low, high]`), a 200 with no `results`, or an undecodable 200 fails the
**whole requested range**: no bar from it is stored, the run is `failed` with
the first reason, and the raw response is retained as rejected evidence under
`daily-bars-rejected`, referenced by the failed run's manifest. The same policy
applies to `ingestion benchmark`. A non-200 response stores no body. Re-run the
range once the cause is understood. `vwap` is the provider-reported
volume-weighted average for the aggregate window (trade population defined by
the provider, not a regular-session VWAP); it must be positive but is **not**
required to lie within the bar's high/low.

**Provenance.** Every fetch records an `ingestion_run` row (status, counts,
timing, the exact request parameters, and the SHA-256 of the raw response).
Raw provider bytes are stored verbatim under `QF_INGESTION_RAW_ROOT`:

```
data/raw/<provider>/<dataset>/<key>/<sha256>.json
```

e.g.
`data/raw/massive/reference-manifest/20260928T031920Z-<run-id>/<sha256>.json`.
Each `.json` is named after its own SHA-256, with a sibling `.meta.json`
describing the request; an artifact whose digest does not match its filename is
detected by the store and refused. Re-fetching a stored response is therefore a
byte-for-byte comparison, not a JSON diff, and the API key is never written to
disk or to the database.

**Adapter discipline.** The Massive client paces requests, retries transient
errors on `408/429/500/502/503/504` (honouring `Retry-After` up to a cap),
treats `401/403` as configuration errors, and decodes every price as
`Decimal` so `199.99` never becomes `199.99000000000000284`.

**Price basis.** Every `daily_price_bar` and `benchmark_price_bar` row carries
an explicit, required `price_basis` (`PriceBasis`, stored `VARCHAR(32)` + `CHECK`,
no default), and the uniqueness keys include it, so one session can hold both:

- `SPLIT_ADJUSTED` — Massive `adjusted=true`: adjusted for stock splits,
  **not** for dividends, not total return. This is the research basis; every
  Step 8 query selects it explicitly (`RESEARCH_PRICE_BASIS`), never relying on
  row order or "the only row". It is also the CLI default
  (`--price-basis split-adjusted`) and what every row that existed before
  migration `0006` was backfilled as.
- `AS_TRADED` — Massive `adjusted=false`: prices as they printed, the candidate
  basis for historical execution/accounting
  (`--price-basis as-traded`). Not called "raw", which already means the
  untouched provider payload bytes. A stock split inside a holding period
  still needs a share-count adjustment; QuantForge has no corporate-action
  engine, so `AS_TRADED` rows do **not** by themselves authorise multi-session
  position accounting across a split.

The Massive client translates the basis to the `adjusted` request parameter and
refuses a response whose own `adjusted` field contradicts the request.
`DailyPriceBar.adjusted_close` is reserved and stays `NULL`; it never carries a
second basis. Every return QuantForge computes from `close` (see
`research/returns.py`) is a **split-adjusted price return, dividends
excluded**. Whether Massive's `volume` is itself split-adjusted under
`adjusted=true` has not been verified live (no key was available when the
as-traded path was built) and nothing relies on it.

**Benchmark.** `quantforge ingestion benchmark` reuses the same
daily-aggregate endpoint, adapter and provenance machinery as
`ingestion daily`, writing into `benchmark`/`benchmark_price_bar` instead of
`security`/`daily_price_bar`. The first (and currently only) benchmark this
project ingests is **SPY, an ETF proxy for the S&P 500 — not the index
itself**. The real index dataset (Massive's `I:SPX`) returns an entitlement
`403` under the currently configured plan; SPY uses the ordinary stocks
aggregate endpoint, which is accessible. `--name` is required on every call
and must say exactly what the symbol represents (e.g. `"SPY (ETF proxy for
the S&P 500)"`, never `"S&P 500 Index"`) — benchmark identity is never
inferred, and an existing row under the same symbol with a different name is
reported as a conflict rather than silently reused or renamed.

**Live-verified, not just unit-tested.** Reference and daily-bar ingestion have
been run against the real Massive API (AAPL reference plus daily bars, including
an idempotent rerun that correctly wrote nothing new), with the raw responses and
`ingestion_run` rows to show it — this is not only exercised through mocks.

**Earnings.** Persistence is implemented and tested — source-aware SHA-256
fingerprinting, idempotent writes, and revision-preserving updates rather than
overwrites, all recorded against an `ingestion_run` for provenance — but there is
no live adapter and no CLI command yet: Massive's Benzinga earnings endpoint
returns an entitlement `403` (`"You are not entitled to this data"`) under the
currently configured plan. See
[`docs/earnings-ingestion.md`](docs/earnings-ingestion.md) for the full design.

## Configuration

All settings use the `QF_` prefix and are read from the process environment, with
an optional `apps/api/.env` file. The compiled-in defaults already match the
Docker Compose stack, so the service works with no `.env` at all.

| Variable            | Default                                       |
| ------------------- | --------------------------------------------- |
| `QF_ENVIRONMENT`    | `local`                                       |
| `QF_DEBUG`          | `false`                                       |
| `QF_APP_NAME`       | `quantforge-api`                              |
| `QF_APP_VERSION`    | `0.1.0`                                       |
| `QF_API_V1_PREFIX`  | `/api/v1`                                     |
| `QF_DATABASE_URL`   | `postgresql+psycopg://quantforge:quantforge@localhost:5432/quantforge` |
| `QF_REDIS_URL`      | `redis://localhost:6379/0`                    |
| `QF_CORS_ORIGINS`   | `http://localhost:3000`                       |
| `QF_CORS_ALLOW_CREDENTIALS` | `true` — rejected at startup if combined with `QF_CORS_ORIGINS=*`, since browsers refuse that header combination |
| `QF_LOG_LEVEL`      | `INFO`                                        |
| `QF_MASSIVE_API_KEY` | *(required to ingest)*                       |
| `QF_MASSIVE_BASE_URL` | `https://api.massive.com`                   |
| `QF_MASSIVE_REQUEST_TIMEOUT_SECONDS` | `20`                    |
| `QF_MASSIVE_MAX_RETRIES` | `3`                                      |
| `QF_MASSIVE_RETRY_BASE_DELAY_SECONDS` | `0.5`                 |
| `QF_MASSIVE_MIN_REQUEST_INTERVAL_SECONDS` | `0.2`             |
| `QF_MASSIVE_MAX_PAGES` | `50`                                       |
| `QF_INGESTION_RAW_ROOT` | `data/raw`                                |

`QF_DATABASE_URL` must use the `psycopg` driver. A bare `postgresql://` URL is
rewritten to `postgresql+psycopg://` automatically, because SQLAlchemy would
otherwise fall back to `psycopg2`, which is not a dependency. A non-PostgreSQL
scheme is rejected outright.

Neither the engine nor the Redis client is created at import time. Importing this
package and calling `create_app()` opens no socket, so the service starts and
serves liveness with both datastores stopped.

## Health endpoints

| Endpoint                   | Purpose                                            |
| -------------------------- | -------------------------------------------------- |
| `GET /health`              | liveness; never touches a datastore                |
| `GET /api/v1/health`       | the same contract, versioned                       |
| `GET /api/v1/health/ready` | readiness; probes PostgreSQL and Redis, `503` if down |

## Companies API

`GET /api/v1/companies` and `GET /api/v1/companies/{cik}/financials` (read-only) are described, with the demonstration
validation-database workflow, in the root [README](../../README.md#companies) and ["View the demo data"](../../README.md#view-the-demo-data).

## Commands

```bash
uv sync --all-groups    # install dependencies

# Migrations
uv run alembic upgrade head
uv run alembic current
uv run alembic history
uv run alembic revision --autogenerate -m "describe the change"

# Run
uv run uvicorn quantforge.main:app --reload

# Ingestion (needs QF_MASSIVE_API_KEY and a live database)
uv run quantforge ingestion reference AAPL MSFT
uv run quantforge ingestion daily AAPL --start 2026-01-01 --end 2026-01-31
uv run quantforge ingestion runs

# Quality
uv run pytest -q --cov=quantforge --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

See [Ingestion](#ingestion) below for what the commands do and how the data is
laid out.

Infrastructure lives at the repository root; see the top-level `README.md`.

```bash
docker compose up -d
docker compose ps
```

## Tests

The default run needs no Docker: the whole schema is verified by inspecting
`Table` metadata and by validating Pydantic schemas, both of which are pure.

```bash
uv run pytest -q          # everything, no Docker required
uv run pytest -m "not integration" -q
```

### Integration tests

The `integration` marker needs a real PostgreSQL server, because the point is to
prove PostgreSQL actually enforces what the metadata claims — `CHECK`
violations, `NUMERIC` scale, `TIMESTAMPTZ` round-trips, partial unique indexes.
Those are never checked against SQLite.

```bash
docker compose up -d
uv run pytest -m integration -q
```

The suite creates and drops its own `quantforge_test` database, applies the real
Alembic migration chain to it, and isolates each test inside an outer
transaction with a SAVEPOINT so a failing test cannot leak rows into the next.
The migration-lifecycle tests run in a *separate* scratch database, because they
downgrade and re-upgrade the schema, which must not disturb the shared one. The
suite skips cleanly when no server is reachable, and derives its admin DSN from
`QF_DATABASE_URL` (override with `QF_TEST_DATABASE_ADMIN_URL`) so it follows your
local configuration instead of hard-coding a second credential.

```bash
uv run pytest -m integration -q -x   # stop at the first failure
```
