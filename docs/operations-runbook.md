# QuantForge operations runbook

For whoever deploys, runs and recovers QuantForge. It states what the system guarantees, the order to
do things in, and what **not** to do. Nothing here needs a provider key, and no step invents data.

## 1. What is running

| Piece | Role | State | Needed to serve? |
|---|---|---|---|
| PostgreSQL | every fact, run record and saved research record | **the** system of record | yes |
| Raw evidence root (`QF_INGESTION_RAW_ROOT`, default `data/raw/`) | provider bytes and run manifests, content-addressed | append-only files the database's provenance points at | no (provenance, not serving) |
| API (`uvicorn quantforge.main:app`) | read-only HTTP; every route is a `GET` | stateless | - |
| Web (`next start`) | the research terminal; reads the API server-side | stateless | - |
| Redis | reserved; **no serving path uses it** | none | no |
| Massive, Finnhub, SEC | ingestion sources | external | **no** - only the ingestion commands call them |

The API has **no authentication**. Deploy it on a private network or behind a reverse proxy that provides
access control; do not expose it directly. Production CORS lists only the origins you name.

## 2. Configuration

Everything is a `QF_*` environment variable; `.env.example` tags each one `[serve]`, `[ingest]` or `[dev]`.

* **To serve** you must set `QF_ENVIRONMENT=production`, `QF_DATABASE_URL` (a real login) and
  `QF_CORS_ORIGINS` (your web origin(s), explicit, no `*`).
* In production the process **refuses to start** if `QF_DATABASE_URL` is still a development login
  (`quantforge`/`quantforge` or the compose password), if `QF_CORS_ORIGINS` was not set, or if `QF_DEBUG`
  is true. The error names the setting, never its value.
* The same guards apply to **every process that loads the settings** - the API, `alembic` and the
  ingestion CLI - so the shell you migrate or ingest from needs the same `QF_ENVIRONMENT`,
  `QF_DATABASE_URL` and `QF_CORS_ORIGINS` (put them in the shared environment or `.env`).
* **Database login.** A role named `quantforge` with no password, or with a development password, is
  refused (also when percent-encoded or given as `?password=`); use a distinct production role.
  The serving role needs `SELECT` on `alembic_version`, or readiness reports `schema: unknown`
  forever. Connection poolers that reject the `options` startup parameter (PgBouncer without
  `ignore_startup_parameters=options`) need that setting, or a direct connection, because the statement
  timeout travels as a connection option.
* **Never required to serve:** `QF_MASSIVE_API_KEY`, `QF_FINNHUB_API_KEY`, `QF_SEC_USER_AGENT`,
  `QF_REDIS_URL`, `QF_INGESTION_RAW_ROOT`. Set `QF_REDIS_URL=` (blank) if there is no Redis, so readiness
  reports `not_configured` instead of a recurring `unavailable` warning. A missing provider key fails the ingestion command that needs it,
  with a message naming the variable; the API is unaffected.
* Database timeouts: `QF_DB_CONNECT_TIMEOUT_SECONDS` (default 5) and `QF_DB_STATEMENT_TIMEOUT_SECONDS`
  (default 60, enforced by PostgreSQL on every connection the API and CLIs open; migrations use their own
  connection). Raise the statement timeout only for a deliberately long bulk ingestion.

## 3. Probes

| Probe | Question | Touches | Use it for |
|---|---|---|---|
| `GET /health` (or `/api/v1/health`) | is the process answering HTTP? | nothing | container liveness / restart decisions |
| `GET /api/v1/health/ready` | can this binary serve its contract? | PostgreSQL + the Alembic revision | load-balancer / orchestrator traffic decisions |

The API reads its migration scripts from the source tree it runs from, so **deploy it as a source
checkout or an editable install**. A wheel install carries no scripts: it can never establish its head
and stays `503 schema: unknown` (fail-closed, with `expected: null`).

Readiness is `200` only when PostgreSQL answers **and** its Alembic revision equals this binary's single
migration head exactly. Otherwise `503` with a typed body:

* `schema.state: "mismatch"` - the database is behind or ahead of the binary. Do **not** try to make it ready
  by editing `alembic_version`. Behind: run the migration (section 5). Ahead: you deployed older code than
  the database; deploy the matching code or restore the matching backup.
* `schema.state: "unknown"` - the revision cannot be read (no version table, empty, several rows) or the
  binary's own migration scripts are unreadable. A brand-new empty database is `unknown` until migrated.
* `dependencies.database.state: "unavailable"` - PostgreSQL is down or too slow (the probe's statements are
  capped at 5 s).
* `dependencies.redis` is reported (`ok` / `unavailable` / `not_configured`) and has `gating: false`: a
  Redis outage never makes the service not ready. **If a future feature makes Redis part of serving, that
  change must make it gating and add the tests that prove it.**

A provider outage never affects either probe; providers are never contacted by them. The probes log one
concise line per failure (exception class names only, no traceback, no URL).

## 4. Backup

A consistent backup is **a PostgreSQL dump and a copy of the raw evidence root, taken together**. The
database's provenance (`ingestion_run.raw_artifact_path`, content hashes) points at files in the raw
root; one without the other leaves references that cannot be checked or evidence nothing refers to.

```bash
# Stop ingestion first (no CLI running). The API may stay up: it only reads.
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
# 1. Database FIRST (libpq connection: use PG* variables or a postgresql:// URL without "+psycopg")
pg_dump --format=custom --no-owner --file "quantforge-$STAMP.dump" "$PG_CONNECTION"
# 2. Raw root SECOND, and keep the pair together
tar -C "$(dirname "$QF_INGESTION_RAW_ROOT")" -czf "quantforge-raw-$STAMP.tar.gz" "$(basename "$QF_INGESTION_RAW_ROOT")"
shasum -a 256 "quantforge-$STAMP.dump" "quantforge-raw-$STAMP.tar.gz" > "quantforge-$STAMP.sha256"   # sha256sum on Linux
```

Order matters because raw files are content-addressed and never rewritten or deleted: a raw copy taken
**after** the dump contains every file the dump refers to (plus, at worst, harmless extras written in
between). This is **not an atomic snapshot** - quiesce ingestion if you need the two to match exactly.
Store the pair off the machine. A backup you have never restored is not a backup (section 7).

## 5. Deploy

Exact schema matching means a binary and its database must change **together**; there is no
zero-downtime window with one migration step. Plan a short maintenance window.

1. **Back up** (section 4): the database dump and the raw root. Do not skip this: it is the rollback.
2. **Stop ingestion**; let any CLI finish or abort it (a failed run rolls back and is safe to retry).
   **Stop or drain the API and web processes** before touching the tree: a running Python process lazily
   imports from the directory it was started in, so replacing files under it can mix old and new code.
   (Or deploy into a fresh directory and switch over.)
3. **Deploy the new code** to the host (the API reads the migration scripts from the same tree it was
   deployed in; that is how it knows its head). Do not start it against the old schema expecting it to be
   ready - it will correctly answer `503 mismatch`.
4. **Migrate explicitly**: `cd apps/api && uv run alembic upgrade head`. Nothing migrates automatically,
   ever. Then `uv run alembic current` must show the head.
5. **Start the API and the web server** on the new code. (An old API process left running would turn
   `503 mismatch` the moment the migration commits - the readiness check working - which is why step 2
   stops it.)
6. **Require liveness**: `GET /health` is `200`.
7. **Require readiness**: `GET /api/v1/health/ready` is `200`, `schema.state` is `compatible`.
8. **Run the release smoke** (section 9). Only then put the instance back in rotation.

If step 4, 7 or 8 fails: stop, keep traffic off, and follow section 6.

## 6. Rollback - read this before you need it

**The rollback of record is: restore the pre-deploy backup pair and redeploy the previous code.**
`alembic downgrade` is not the path of record:

* **Migration `0014` cannot be downgraded once SEC filings exist.** Bulk SEC ingestion stores
  `sec_filing.accepted_at = NULL` by design ("exact acceptance instant not established"). The `0014`
  downgrade refuses while any such row exists, because the previous schema requires a value and the only
  way to satisfy it is to **invent** an acceptance time. Do not fabricate `accepted_at`, do not delete the
  rows to get past the check, and do not edit `alembic_version`. If SEC filings have been ingested since
  `0014` was applied, going back means restoring the backup taken before it.
* Before any SEC data exists, `alembic downgrade -1` from `0014` works. Even so, rehearse a downgrade on a
  restored copy, never first on production.
* Rolling back **code only** leaves the database ahead of the binary: readiness will (correctly) say
  `503 mismatch`.

A deploy that fails *after* `alembic upgrade head` succeeded is rolled back by restoring both halves of the
backup (section 7) and starting the previous release.

## 7. Restore

Restoring brings back a past moment: any ingestion since the backup is gone and must be re-run (it is
idempotent, so re-running is safe).

1. **Stop writes**: stop the API, the web server and every ingestion process.
2. **Restore the database** into an empty database:
   `createdb quantforge_restore` then
   `pg_restore --no-owner --dbname quantforge_restore quantforge-<STAMP>.dump`
   (or restore over the target after you have kept a copy of what is there).
3. **Restore the matching raw root**: extract `quantforge-raw-<STAMP>.tar.gz` to the path
   `QF_INGESTION_RAW_ROOT` points at. Use the pair taken together; restoring a raw root from another time is
   not a restore.
4. **Check the pieces**: `shasum -a 256 -c quantforge-<STAMP>.sha256` (`sha256sum -c` on Linux); `uv run alembic current` shows the head the
   dump was taken at.
5. **Start the code that matches that head** - the release that was running when the backup was taken.
6. **Readiness**: `GET /api/v1/health/ready` is `200` and `compatible`. A `mismatch` means code and dump are
   from different releases.
7. **Verify**: run the release smoke with `--verify` (section 9) - saved research records are re-verified
   against the restored evidence and should report `MATCH`.

This procedure is not atomic and does not claim to be: the dump and the raw copy were taken separately
(section 4). If a manifest names a file the restored raw root lacks, the run's provenance is incomplete -
find the right raw copy rather than editing the database.

## 8. Day-to-day operations

### A provider is down or rate-limiting
Nothing to do for serving: readiness does not look at providers and every stored page keeps working.
Ingestion commands fail on their own (`provider request failed ...`, exit 1) and record a `failed` run.
Wait, then re-run the same command - it is idempotent.

### An ingestion run failed or fell short
```bash
cd apps/api
uv run quantforge ingestion runs --status failed     # or --status partial
```
1. A failed command printed a footer: the **run id**, `raw evidence: <manifest or none>`, `written: <n>` and
   `safe to retry: yes`. `runs` shows the same, plus the redacted reason (`error:`).
2. A `partial` run's `error` reads `N record(s) failed; first: <label>: <reason>` - the first reason only.
   Re-run with the global `--log-level INFO` option (`quantforge --log-level INFO ingestion ...`) for the rest.
3. `raw manifest:` names the run's manifest (a JSON file under the raw root) listing every response the run
   kept, each with its SHA-256. Paths containing `-rejected/` are responses the system **refused** (could not
   decode, validate or map); the others were accepted. Read them to see exactly what the provider sent.
4. Decide: a transient cause (timeout, rate limit, outage) - retry. A deterministic one (a provider
   conflict, an unattributable event, a package that changed) - fix the cause or reconcile the source first;
   retrying repeats the failure.
5. **`safe to retry: yes` means a retry will not duplicate or corrupt data** (a failed run rolls its writes
   back and every write is idempotent). It does **not** promise the retry will succeed.
6. Never edit rows by hand to "fix" a run.

### Known limits (carried, not hidden)
A hard process death between receiving a reference response and classifying it can lose those bytes; an
oversized response is logged, not retained; Ctrl-C can omit the failure footer; Massive stock-splits /
snapshots / events and the SEC client still drop an undecodable 200's bytes.

## 9. Release smoke

Read-only: GET requests only; it saves nothing, ingests nothing, calls no provider, runs no migration.

```bash
# with the API and the PRODUCTION web build running
QF_API_URL=http://127.0.0.1:8000 QF_WEB_URL=http://127.0.0.1:3101 node scripts/release-smoke.mjs
#   --build       run `npm run build` first        --start-web   serve that build for the run
#   --verify      also open a saved record's verify page (asks the backend to re-verify; still read-only)
#   --no-browser  API + web reachability only
# or, from apps/web:  npm run smoke:release
```

Phases (the first failure stops the run, prints `FAIL <phase>: <reason>` and exits 1):
`liveness` -> `readiness` (ready, database ok, schema compatible) -> `web` (200) -> `browser`
(every page 200, no 5xx, zero axe violations, no horizontal overflow at 1440 px and 390 px) ->
`unchanged` (stored counts and readiness are the same after the run as before it). It needs Google Chrome
installed (the browser phase uses the system Chrome through Playwright) and at least one stored earnings
event and FSDS-backed filing to visit.

## 10. Quick reference

| Symptom | Meaning | Do |
|---|---|---|
| API refuses to start, names `QF_DATABASE_URL` | production with a development login | set a production database URL |
| API refuses to start, names `QF_CORS_ORIGINS` | production without explicit origins | list your web origin(s) |
| readiness `503`, `schema: mismatch` | binary and database are at different revisions | migrate (behind) / deploy matching code (ahead) |
| readiness `503`, `schema: unknown` | no readable revision | migrate a new database; otherwise restore |
| readiness `200`, `redis: unavailable` | Redis is down; nothing serving uses it | investigate at leisure |
| `alembic downgrade` refuses at `0014` | SEC filings with unestablished `accepted_at` exist | restore the pre-migration backup |
| ingestion exit 1 + footer | a run failed and rolled back | section 8 |
