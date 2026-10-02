# Earnings ingestion design (Step 7.x)

Status: canonical contract (7.1) + idempotent persistence, provenance and run
accounting (7.3) shipped. The live provider adapter (7.2) is **not** shipped:
Massive/Benzinga earnings return HTTP 403 ("You are not entitled to this
data"), so no adapter, DTOs or mapper exist yet. 7.3 is implemented and tested
against the 7.1 `EarningsProvider` contract with stub providers; wiring the
real provider in 7.2 will not touch the writer.

## What 7.1 shipped

- `CanonicalEarningsEvent` in `src/quantforge/ingestion/canonical.py`.
- `EarningsProvider` protocol + `EarningsResult` in
  `src/quantforge/providers/earnings.py`.
- Focused tests and this design note.

## What 7.3 shipped

- `EarningsIngestor` and `earnings_fingerprint` / `resolve_company_id` in
  `src/quantforge/ingestion/earnings.py`; `EarningsIngestionService.ingest_earnings`
  in `src/quantforge/ingestion/service.py`.
- Migration `alembic/versions/0004_earnings_ingestion.py`:
  `earnings_event.fingerprint` (`VARCHAR(64)` NOT NULL) + the unique index
  `uq_earnings_event_source_company_fingerprint` on
  `(source_id, company_id, fingerprint)`, and a nullable
  `earnings_event.ingestion_run_id` FK + index (run attribution, mirroring
  `daily_price_bar`).
- Integration tests against real PostgreSQL (exact facts, idempotency,
  revisions, cross-source coexistence, date-aware resolution, run/raw
  provenance, transaction shape).
- No CLI and no analytics/frontend work.

## Normalization boundary

```
provider REST API
    -> provider adapter          (provider-specific, e.g. providers/massive)
    -> raw response capture      (verbatim bytes, stored before interpreting)
    -> provider DTOs             (provider-specific, stay in the adapter)
    -> provider-specific mapper  (7.2; NOT written -- no entitlement)
    -> CanonicalEarningsEvent    (provider-independent)
    -> EarningsIngestionService  (resolve ticker -> company, dedup, store)
    -> EarningsEvent             (the persisted fact)
```

A provider's field names, status codes and JSON shapes are allowed to appear
only inside its own adapter. The canonical type is the invariant boundary:
the 7.3 writer tests run with no HTTP client and no vendor in sight, exactly
as the daily-bar writer does today. Raw bytes are captured and stored by the
ingestion service before any interpretation, so a late discovery of a mapper
bug can always be replayed from the captured artifacts.

## Point-in-time rules

- `event_timestamp` = the instant the event occurred or is scheduled, exactly
  as the source stated it. **Not** when QuantForge learned about it. Must be
  timezone-aware; naive values are rejected at canonical construction.
- `observed_at` = when the source made the information available, only when the
  source genuinely stated it. `None` means undisclosed, **never** "equals
  ingested_at".
- `ingested_at` = when QuantForge actually stored the fact, stamped by the
  ingested clock, never the fetch's `NOW`.
- Never manufacture `observed_at`. In particular, do not derive it from the
  event timestamp, the session close, or the moment of fetch.
- Never infer before/after-market `timing` from the wall-clock hour of
  `event_timestamp`. `UNKNOWN` is the honest answer when the source declares
  nothing.
- No forward returns, EPS-surprise, revenue-surprise or abnormal-return
  computations in this milestone. Surprise belongs to the analytics layer,
  where the methodology is versioned and re-runnable.

## Identity and revision policy (as built)

`(company_id, fiscal_year, fiscal_quarter)` is **not** unique by design. One
fiscal period legitimately has several observations: a preliminary release and
a final one, a restatement, or two vendors reporting the same period.

Exact-observation identity is `(source_id, company_id, fingerprint)` where
`fingerprint` is a deterministic SHA-256 over the *factual fields* of the
observation: fiscal year, fiscal quarter, event timestamp, timing, all four
figures, currency, and `observed_at`. The digest intentionally excludes the
ticker (the key already carries `company_id`) and `ingested_at` (a re-fetch of
the same fact must collide).

The policy is therefore:

1. **Dedup within a source** -- same source, same company, same facts: the
   unique index makes the re-fetch a no-op (counted `records_skipped`), done
   atomically with `INSERT ... ON CONFLICT DO NOTHING`, so concurrent ingestors
   cannot double-insert.
2. **Revisions within a source** -- same source, same company, facts differ: a
   *new row* is inserted and preserved next to the original. A changed estimate
   or restatement is history to keep, never an overwrite and never a merge.
3. **Across sources** -- rows from different `source_id`s are never collapsed.
   Which source wins for a period is a downstream reconciliation/reporting
   question (source priority), not a write-time overwrite.

Why a digest column and not a plain multi-column unique? The four figures,
`fiscal_quarter` and `currency` are nullable, and PostgreSQL unique semantics
treat `NULL` as distinct from `NULL` -- so a unique index over the natural
columns would silently accept two identical observations that happen to lack
an optional figure, which is precisely the duplicate a re-fetch must skip. A
single NOT NULL digest collides when, and only when, the whole fact set is
equal. The digest is *derived data*, not caller input: its single definition
lives in `earnings_event_fingerprint` (`models/earnings.py`), the model
recomputes it from the row's own columns on every ORM write, and the writer
computes the same value from the canonical fields, so the two paths cannot
drift.

## Date-aware company resolution

A canonical event names a ticker, which resolves to a *company*. Tickers are
reused by different issuers over time, so "which company" is answered by the
event's own date: the listing whose half-open interval `[valid_from, valid_to)`
covers the event date is the issuer of record. A ticker with no covering
listing, or covered by listings from two different issuers on that date, raises
`UnknownIssuerError`: the event is counted `records_failed` and the run finishes
`partial`. No company is ever fabricated or guessed.

## Provenance and run accounting

- Every written row carries `ingestion_run_id`, and the `IngestionRun` that
  produced it is stamped with `records_received/written/skipped/failed`. The
  two commit or roll back together -- the writer never commits; the caller owns
  the transaction.
- `records_failed == 0` succeeds the run; any failures (unresolvable ticker)
  leave it `partial`; a provider exception marks it `failed` with the classified
  error type and re-raises.
- Raw captures are stored with `RawResponseStore` and each `IngestionRun`
  references its capture artifact (`raw_content_sha256`), so every row is
  traceable to the verbatim response that produced it.

## Open questions for 7.2

- Which provider the first earnings adapter targets, and its actual field
  names, pagination and revision semantics (blocked on data entitlement).
- Whether `get_earnings` range semantics needs an explicit venue/calendar input,
  or whether the adapter's own day boundary is sufficient (the contract leans
  on the latter).
- How "scheduled but not yet reported" events (event exists, figures absent)
  should be handled: stored immediately or only once a figure is present.
- Whether fiscal-year/fiscal-quarter normalization is provider-free of a
  calendar mapping, and where that mapping lives.