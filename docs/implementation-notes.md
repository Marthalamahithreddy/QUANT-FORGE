# Implementation notes

Step-by-step notes on the data and research layers, moved here **unchanged** from the root `README.md` so the README
can stay an entry point. They were written as each layer landed, so a sentence may describe the state *at that step*.
Where a later step changed the picture, the table below, the code and the root README are authoritative:

| Older statement below | Current state |
|---|---|
| EPS extraction: "no endpoint, no UI; nothing imports it yet" | The filing EPS API and the `/filings` pages consume it (sections "Filing EPS API" and "Filings index" below) |
| "Comparative selection is not built" (real-data validation of 2026q1) | Built: "Same-accession comparative EPS" below |
| Saved research records: "No endpoint, no frontend" | The saved-records API and the Saved research pages exist (see the "Saved research records API" section) |
| Research observation API: "no frontend" | The research workspace (`/research/events/[eventId]`) exists; see `apps/web/README.md` |
| "there is no browser end-to-end framework" (filings workspace) | Playwright end-to-end tests exist; see `apps/web/README.md` |
| Companies, revenue and net income | Not described here; see the root README, "Companies" |

## Project summary at the foundation steps (historical)

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
(`quantforge.backtest`; see the root README for the current state).

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
hand-written-migration caveat, are in [`apps/api/README.md`](../apps/api/README.md).

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
uv run quantforge ingestion earnings AAPL --start 2026-01-01 --end 2026-03-31 \
                                                  # earnings events (needs QF_FINNHUB_API_KEY)
uv run quantforge ingestion runs                  # recent runs, newest first
uv run quantforge ingestion runs --limit 20 --dataset daily-bars
uv run quantforge ingestion runs --status partial # only runs that fell short (shows why)
```

A failed run prints its run id, where its raw evidence is (`raw evidence: none` when nothing was kept),
how many rows it wrote and `safe to retry: yes`: a failed run rolls its writes back and every write is
idempotent. A `partial` run records the first failure on its row (`N record(s) failed; first: ...`).

| Command | Needs | What it stores |
|---|---|---|
| `ingestion reference TICKER...` | `QF_MASSIVE_API_KEY` | issuer, security and listing identity |
| `ingestion earnings TICKER --start D --end D` | `QF_FINNHUB_API_KEY`, reference first | earnings observations (revisions kept) |
| `ingestion daily TICKER --start D --end D` | `QF_MASSIVE_API_KEY`, reference first | split-adjusted (or `--price-basis as-traded`) daily bars |
| `ingestion benchmark SYMBOL --start D --end D --name N` | `QF_MASSIVE_API_KEY` | benchmark levels with an explicit identity |
| `ingestion sec-filings CIK` | `QF_SEC_USER_AGENT` | SEC filing **identity** (`accepted_at` stays NULL) |
| `ingestion sec-filing-acceptance ACCESSION [--verify]` | `QF_SEC_USER_AGENT` | one filing's `accepted_at`, from its official EDGAR header |
| `ingestion sec-fsds PACKAGE.zip --accession A` | a local FSDS ZIP, no network | accession-bound numeric facts |
| `ingestion runs [--status S] [--dataset D]` | database only | read-only run history with failure reasons and evidence manifests |

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
plan. See [`apps/api/README.md`](../apps/api/README.md#ingestion) for the full
detail.

## Availability evidence and SEC filing metadata

`earnings_event.observed_at` is only meaningful with `observed_at_basis`
(`AvailabilityBasis`), stored together or not at all (a database CHECK):

- `SOURCE_PUBLICATION` — the source itself states when this observation was
  published. The only basis currently approved for point-in-time/execution use.
- `SEC_FILING_ACCEPTANCE` — EDGAR accepted a specific filing then. Defined, but
  **not approved** for point-in-time use yet (the exact timezone reading has not
  been validated against live SEC data, and no filing-based signal defines the
  strategy it would serve). It proves acceptance by EDGAR — not first public
  disclosure; a press release can precede an 8-K by up to four business days.

Which bases count is decided in one place
(`research/availability.py: APPROVED_AVAILABILITY_BASES`). Finnhub and Massive
leave both fields NULL, so no current earnings row is point-in-time eligible.

`quantforge ingestion sec-filings <CIK>` stores SEC filing **identity metadata only**
(accession, form, filing/report dates, primary document, 8-K item codes) in `sec_filing`
(10-K, 10-Q, 8-K and their amendments, each amendment its own row), for the stored company
carrying that `company.cik`. It needs `QF_SEC_USER_AGENT` (an application name plus a contact,
per the SEC's fair-access policy; never defaulted or stored). **The bulk submissions JSON never
supplies `accepted_at`**: its `acceptanceDateTime` has no documented timezone, and the 11.2G
investigation found it equal to the official EDGAR header for some registrants and exactly one
Eastern UTC offset later for others, changing over time for the same accession. `accepted_at` is
NULL ("exact acceptance instant not established") until
`quantforge ingestion sec-filing-acceptance <ACCESSION> [--cik CIK] [--verify]` sets it from that one
accession's official EDGAR header (one request, Eastern wall clock converted with the IANA zone,
identity-checked against the stored filing, raw header retained). There is no bulk or
"enrich all" mode; NULL does not mean the filing is invalid or unavailable, and accounting,
the filing API and research never read it. It is acceptance **metadata** only, not public
availability, and `SEC_FILING_ACCEPTANCE` stays unapproved. Results are in deterministic
filing-date order (accession as tie-breaker), not acceptance order. No EPS, signal or document
download exists here.

`quantforge ingestion sec-filings-history <CIK> [--accession ACC]... [--max-history-files N]`
stores the **identity** metadata of older filings that fell out of `filings.recent`, by
following the history files (`filings.files`) the SEC's own parent response names, so
older accession-bound FSDS facts can bind to a stored filing. It is bounded: one explicit
CIK per run, one parent request plus at most `--max-history-files` history-file requests
(a registrant needing more is refused up front, never truncated), and with `--accession`
only the files that could hold a still-unresolved target are fetched. There is no
"all companies" mode. Every response is kept content-addressed with its SHA-256, byte
length, request path and QuantForge's own retrieval time (never an SEC publication time);
identical bytes are deduplicated and a changed body under the same URL is a new artifact,
never an overwrite (the SEC documents no immutability for history files). A stored filing
is never rewritten: identical metadata is a skip, differing identity metadata is a loud
conflict (the acceptance instant is not compared, and a header-established one is never erased). A history file that cannot be fetched or
reconciled with its stated `filingCount` fails the whole run; a record that cannot be
mapped (a malformed identity field) is rejected and makes the run `partial`; a date-only or
odd acceptance text no longer blocks a valid identity. The report counts what was
looked at and never claims the registrant's history is complete. This is identity and
provenance only: no availability, PIT or disclosure meaning, and it does not ingest facts.

## Historical stock splits and the corporate-action guard

`quantforge ingestion stock-splits <TICKER> --start D --end D` fetches one ticker's
splits from Massive (`/stocks/v1/splits`) into `stock_split` (migration `0009`), attached to the
**security** that held the ticker on each split's execution date, never to a ticker or
today's symbol. `old_shares` / `new_shares` follow Massive's `split_from` / `split_to` (a 2-for-1
split is 1 → 2; a 1-for-10 reverse split is 10 → 1); the provider's classification must agree with
the ratio or the record is rejected. `execution_date` is the effective date (all trading on it is
already adjusted); it is **not** an availability time and no announcement time exists. Nothing is
adjusted: no shares, prices or cost basis.

An empty `stock_split` table does **not** mean no split happened. `stock_split_coverage` records the
security/date ranges for which a complete, clean fetch ran (any rejected record fails the run and
writes none); the range must end 7+ days before today, be inside the plan's history, and is recorded
only through a *current* ticker of the security. `quantforge.market.evaluate_split_guard` /
`StockSplitRepository.guard` refuse a holding from entry session `E` to exit session `X` iff a
split takes effect on `S` with `E < S <= X` (`BLOCKED_BY_SPLIT`), and return `CLEAR` only when coverage proves
`(E, X]` has none; otherwise `COVERAGE_UNAVAILABLE`. The guard is not wired into research or
execution (no fill engine exists). Coverage depends on the stored listing history: reference
ingestion sets a listing's `valid_from` to the date QuantForge first observed it, so a ticker cannot
be shown to belong to a security before that date, and historical coverage is refused (safely) until
real listing history exists. A split inside the requested range but outside the proven ticker
period fails the run and records no coverage. The live Massive probe is outstanding (no key configured).

## Historical listing identity

`Listing.valid_from` is when QuantForge **first observed** a listing, not when it began trading, so it proves
nothing about earlier dates. Source-stated history is stored separately as single-date
`listing_history_fact` rows (migration `0010`): a dated snapshot (`ticker observed on D`), a ticker-change
event (`ticker started on D`) and a last-traded date, each with its source and run, attached to a security
through its **share-class FIGI** (`IdentifierType.FIGI_SHARE_CLASS`; the legacy `FIGI` type does not say which
FIGI level it holds and is never trusted as an anchor; the CIK is company identity only). Intervals are never
stored: `quantforge.identity` derives them deterministically from facts and first-observed listings, and one
resolver (`HistoricalListingResolver`) answers every consumer (research alignment, earnings ingestion, the
earnings API, split ingestion), returning `RESOLVED` / `NO_EVIDENCE` / `AMBIGUOUS` / `CONFLICT` with an
evidence kind. Two snapshots never become a continuous interval, gaps are never filled, sources never outrank
one another (a disagreement is a conflict), and no exchange continuity is inferred. Listing identity is
reference-data reconstruction; it never feeds `observed_at` or a point-in-time claim.

`quantforge ingestion listing-snapshot TICKER --date D`, `listing-events TICKER` (Massive's EXPERIMENTAL
ticker-events, anchored by a recent snapshot) and the read-only `listing-eras TICKER --start --end` are the
only commands; no live Massive validation has run (no key configured), so the ticker-change event-date meaning
(treated as an inclusive start) and retired-symbol behaviour remain unverified.

## Accession-bound financial facts (SEC FSDS)

`quantforge ingestion sec-fsds PACKAGE.zip --accession ... | --cik ...` extracts numeric facts from one **local**
SEC Financial Statement Data Sets quarterly ZIP into `financial_fact` (migration `0011`). FSDS is QuantForge's
**first structured extraction source** for accession-bound facts; it is not, by itself, a point-in-time source of
record. The model is accession-bound, not FSDS-bound: `financial_fact_artifact` (one row per package *content*,
SHA-256 identity; the ZIP itself lives in the raw store, streamed, never in a row), `financial_fact_submission`
(what the artifact stated about one filing) and `financial_fact` (one fact bound to exactly one existing
`sec_filing`). Only 10-K, 10-K/A, 10-Q and 10-Q/A submissions are read.

* **A fact is a source concept, not "EPS".** Identity is the FSDS NUM key with the accession resolved to a stored
  filing: tag **and** taxonomy version (an issuer extension's version is the accession that defined it), period end,
  `qtrs`, unit, `segments` and `coreg`. `value` is exact `Decimal` (the source caps it at 4 dp and states no
  precision attribute) and is not part of the identity: a changed value is a conflict, never an overwrite.
  A quarter (`qtrs=1`) and a year-to-date amount (`qtrs=2`) of one concept are two facts and nothing collapses them.
  Period ends are the source's month-end-rounded dates, never mapped to a calendar quarter; units are never
  converted; dimension/co-registrant text is kept verbatim. The SEC documents a NULL `coreg` as the consolidated
  entity, but QuantForge stores only the absence (`''`) and applies no such label; absent `segments` is likewise
  not "consolidated". A NULL `value` (the SEC marks it nullable) is stored as NULL: not zero, not a missing row, and
  not a valid numeric observation.
* **Existing filings only.** The accession must already be a `SecFiling` (ingested by `sec-filings`), its company's CIK
  must equal the package's registrant CIK, and the form and filing date must agree; otherwise the run fails and no
  fact is written. An amendment is its own accession and its own facts.
* **No availability.** `SecFiling.accepted_at` (when set from the EDGAR header; NULL otherwise) is when EDGAR accepted the filing; a bulk package is published
  later and the SEC does not time-stamp it per package, so nothing here is point-in-time available and no table
  carries an availability column. `SEC_FILING_ACCEPTANCE` stays unapproved.
* **Bounded and safe.** At least one `--accession`/`--cik` is required; NUM is streamed and batched; any rejected
  selected row fails the run and stores nothing. A value must fit the source's NUMERIC(28,4) exactly (at most 24
  integer and 4 fractional digits, plain decimal text; never rounded, never via float); anything else fails the run.
* **Changed package = refused, and no revision workflow exists.** The same package name arriving with different bytes
  is refused and both byte sets stay in the raw store; nothing is changed. The SEC re-posted reprocessed data sets in
  December 2024, so a legitimate republication is real, but this checkpoint can only *detect and preserve* it: such a
  package cannot be ingested until an explicit revision workflow is built. Artifact identity is package name **and**
  SHA-256, never a file name; identical bytes rerun idempotently.
* **Accessions: explicit vs CIK-wide.** An explicit `--accession` with no stored `SecFiling` fails the run; a `--cik`
  selection containing one reports it as `unbound` and the run is `partial` (the CLI prints selected / bound / unbound /
  not-read counts). `partial` is not a coverage statement: a fact missing from `financial_fact` is absent from the
  locally ingested accessions only, never evidence the source lacks it, and no API converts "not stored" into "absent".
  Because `sec-filings` ingests `filings.recent` only, older accessions have no local `SecFiling` and cannot be bound, so
  historical coverage is bounded by locally known filings (older-submissions paging is future work).
* TAG and PRE are not read (the version identifies extensions; statement classification belongs to a later
  selection layer). The `prevrpt` flag is never read: it encodes knowledge from after the filing. The SEC
  CompanyFacts API is never used as history. NUM `footnote` text is not stored (it is not part of the NUM key). No
  signal, YoY, surprise or selection exists yet. The adapter reads local files and needs no network. **Real-package
  evidence (SEC `2026q1`, 5,311 10-K/10-Q-family filings, one official download):** `accepted` is stated as
  `yyyy-mm-dd hh:mm:ss.0` (the readme documents 19 characters), so it is stored as verbatim source text in a
  `VARCHAR(64)` (migration `0012`), never parsed, normalised or given a timezone (none is stated), and it is not
  availability evidence. There is deliberately no shape check on it, so a systematic format change in a future
  package would load without any reject. Still unverified: duplicate NUM rows, whether `value` text ever uses exponent notation (such a
  row is rejected, loudly), and behaviour on a reprocessed package. Two
  concurrent runs ingesting one package name with different bytes are not serialised (single-operator CLI).

## Accession-scoped EPS extraction (descriptive only)

`quantforge.financials` is the first **semantic** layer over `financial_fact`: given one stored accession and an
**explicit** `EpsPeriodClass` (`QUARTER` | `ANNUAL`) and `EpsKind` (`DILUTED` | `BASIC_AND_DILUTED`), it returns one
immutable `EpsFactObservation` or an explicit refusal status. A pure selector (`financials/eps.py`) holds every rule;
a thin adapter (`extract_eps_observation`) only reads the stored rows of that one filing. No migration, no table, no
endpoint, no UI; nothing imports it yet.

* **Question answered:** "does this accession contain exactly one defensible standardized EPS fact for the requested
  accounting semantics?" It never says when a fact was known: no availability field, no `accepted_at` read, no PIT claim.
* **Rules:** 10-Q/10-Q/A support `QUARTER` (`qtrs == 1`) and 10-K/10-K/A support `ANNUAL` (`qtrs == 4`); year-to-date
  rows are never a quarter and **no Q4 is derived**. The fact's period end must equal the filing's own FSDS submission
  period end (same month-end rounding), so prior-year comparatives are never picked. The concept is matched by exact
  tag and a `us-gaap/<year>` version (any historical release; an issuer extension is never mapped). Basic EPS and
  continuing-operations EPS are never substituted. `EarningsPerShareBasicAndDiluted` (defined by the taxonomy as one
  figure reported when basic and diluted are the same) is available only as the explicitly requested kind
  `BASIC_AND_DILUTED`, never as an automatic fallback and never labelled `DILUTED`. Only a fact with empty `segments`
  **and** empty `coreg` is eligible ("non-dimensional under the FSDS representation", not "confirmed consolidated");
  the unit must be exactly `USD`: real FSDS reports a per-share amount's unit as the bare currency code (`CAD` also
  occurs; `USD/shares` was not observed in the sampled package, and another vintage using it would refuse as `WRONG_UNIT`), so selection needs the exact EPS concept **and** the `USD` unit (a `USD` fact is not EPS
  merely because of its unit), and CAD or any other unit is `WRONG_UNIT` (no conversion).
* **Statuses:** `AVAILABLE`, `NOT_INGESTED` (nothing stored; *not* "absent"), `UNSUPPORTED_FORM_PERIOD`, `MISSING`,
  `EXTENSION_ONLY`, `WRONG_UNIT`, `DIMENSIONAL_ONLY`, `COREG_ONLY`, `NULL_VALUE`, `AMBIGUOUS`. More than one eligible
  fact (for example two taxonomy releases in one accession) is `AMBIGUOUS` even when the values agree: no first/last/
  largest tie-break. The result never depends on fact order. An amendment is its own accession and is never preferred.
* **Real-data validation (2026q1):** `SUB.period` equals an EPS `ddate` in 99.93% of filings, `qtrs` 1/4 and the
  dimension/co-registrant policy held, every EPS row carried exactly one us-gaap release, and class-specific EPS appears
  through `ClassOfStock=...;` segments. Comparative selection is not built: 10-Qs normally show one earlier same-`qtrs`
  fact, 10-Ks several, and a unique candidate 12 month-ends earlier usually identifies the comparative, but 52/53-week
  filers (month-end rounding) and fiscal-year changes break that relationship, so a future selector must refuse those
  or find stronger context. No YoY, surprise or signal exists.

### Same-accession comparative EPS (descriptive only)

`select_comparative_eps` / `extract_comparative_eps_observation` (`financials/comparative_eps*.py`) return, for one
accession, the current EPS observation plus **comparative EPS as presented in this filing**, or an explicit refusal
(`ComparativeEpsObservation`, a pure selector plus a read-only adapter; no table, no migration). The comparative may be
restated or reclassified and is **not** the originally reported prior-year EPS: no other accession is ever consulted.
There is no growth, direction, signal or availability claim (EPS change, a separate pure layer, is described below).

* **Current first:** the existing selector decides the current EPS; anything but `AVAILABLE` is `CURRENT_UNAVAILABLE`
  carrying that status, and nothing earlier is searched.
* **Candidates are stored facts, never generated dates:** same accession, exact current tag (no kind fallback), standard
  `us-gaap/<year>` version (any release; extensions and custom tags are not mapped), the **same `qtrs`** (a year-to-date
  amount is never a quarter) and an earlier period end (only these "structurally relevant" facts can make a comparative `MISSING` vs a period
  mismatch; other concepts, other `qtrs` and other accessions never do). The observed period ends are then inspected: only a fact exactly
  **12 month-ends** before the current one (`(cur.year - prior.year) * 12 + (cur.month - prior.month) == 12`, both
  FSDS month-end dates; no day count) is accepted. Several earlier annual columns are normal, not ambiguity.
* **Conservative, not universal:** real 2026q1 data showed 52/53-week filers (an 11-month rounded relationship) and
  fiscal-year changes (9/16/18/20 months). They are refused as `COMPARATIVE_PERIOD_MISMATCH`, with no tolerance and no
  issuer special-casing, so some filers get no comparative until stronger context evidence exists.
* **Then:** dimensions/coreg (`COMPARATIVE_DIMENSIONAL_ONLY` / `COMPARATIVE_COREG_ONLY`), unit equal to the current unit
  (`COMPARATIVE_WRONG_UNIT`, never converted, never "missing"), a single NULL (`COMPARATIVE_NULL_VALUE`), and exactly one
  (`COMPARATIVE_AMBIGUOUS` otherwise, even with equal values or a NULL beside a number). `COMPARATIVE_MISSING` means no
  earlier standard-us-gaap, same-concept, same-`qtrs` fact is stored (same-name extension facts are ignored, never mapped). An amendment is its own accession. The result is order-independent.

### Descriptive EPS change (over a comparative observation)

`compute_eps_change` (`financials/eps_change.py`) is a pure transformation of an **AVAILABLE**
`ComparativeEpsObservation` (it retrieves nothing; the comparative observation is the evidence envelope). Any other
comparative status gives `INPUT_UNAVAILABLE` with nothing calculated. It is descriptive only: no surprise/consensus,
no signal or score, no availability or clock, no persistence, no migration.

* **`eps_change = current - comparative`**: signed, exact `Decimal` (never `abs`; not "absolute change", not "growth").
* **`transition`** (one enum, no contradictory fields): `POSITIVE_INCREASED/DECREASED/UNCHANGED`,
  `LOSS_NARROWED/WIDENED/UNCHANGED` (descriptive loss-magnitude moves), `LOSS_TO_PROFIT`, `PROFIT_TO_LOSS`,
  `ZERO_TO_PROFIT/LOSS`, `PROFIT/LOSS_TO_ZERO`, `ZERO_UNCHANGED`. Zero of any spelling (`-0`, `0.0000`) is one class.
* **`percentage_change = (current - comparative) / comparative`**: a **ratio** (`0.5` = +50%, not x100, like
  `surprise_ratio`), only for a **positive** comparative (`1.00 -> -0.50` is `-1.5`). A zero or negative comparative gives
  `None` plus `PercentageUnavailableReason.ZERO_COMPARATIVE` / `NEGATIVE_COMPARATIVE`; `abs(comparative)` is never used,
  so a narrowing loss is never called positive growth.
* **Arithmetic:** an explicit isolated context (50 significant digits, `ROUND_HALF_EVEN`, traps on), independent of and
  never mutating the process-global Decimal context. The percentage is **not quantized or formatted** in the domain
  (`-1/3` keeps 50 digits); display rounding belongs to a later layer. No float anywhere.

### Reproducibility foundation (Step 10.9A, pure, not persisted)

`quantforge/reproducibility/` defines what identifies a `ResearchObservation` computation: a **request** identity
(effective inputs after defaults), a **methodology** identity (explicit version labels + policy parameters), an
**evidence** identity (ids/hashes of the stored data actually consumed, not database state) and a separate **result**
fingerprint, all SHA-256 over one deterministic canonical JSON (fixed-point decimals, UTC instants, sorted keys; not
JCS). `ReproducibilityLevel` is `COMPLETE` or `PARTIAL`, derived only from evidence gaps (for example a consumed
benchmark bar can never be run-pinned because `benchmark_price_bar` has no `ingestion_run_id`). `verify_rerun` compares a
fresh assembly with a baseline kept by the caller (or two fresh assemblies, which only detects nondeterminism). A match
is never a claim that historical bytes still exist. No API, no migration, no persistence (10.9B).

### Persistent research records (Step 10.9B, internal service only)

Migration `0013` adds a nullable `benchmark_price_bar.ingestion_run_id` (new levels record the run that wrote them; existing
rows stay NULL, never inferred) and `research_observation_record`: an immutable baseline per computation with its own id,
the exact canonical manifest and result as TEXT (JSONB would not preserve the digested bytes), their SHA-256 digests and the
derived level/gaps. Triggers reject UPDATE, DELETE and TRUNCATE. `research_records.save_research_record` assembles twice and
requires agreement, validates hashes, saves PARTIAL records too, returns the existing record for an exact duplicate and refuses
(`ResultDivergenceError`) the same manifest with a different result. `verify_stored_record` reruns with the baseline's own
request through the 10.9A verifier. No endpoint, no frontend, no verification history table.

### Saved research records API (Step 11.0A, read-only)

`GET /api/v1/research/records` (bounded `limit` 1-200, default 50, and `offset`; newest saved first, `id` tie-break) lists
stored baselines as small summaries (record id, fingerprints, derived level and gaps, and what was asked) without the
canonical payloads. `GET /api/v1/research/records/{record_id}` returns one baseline parsed (manifest and result as
structured JSON, decimals exact strings) only after the load service has re-hashed the stored canonical text.
`GET /api/v1/research/records/{record_id}/verify` reruns the stored computation against current stored evidence with the
10.9A verifier and reports `MATCH`, `MANIFEST_CHANGED`, `RESULT_CHANGED` or `EVIDENCE_INCOMPLETE` with changed paths and
fingerprints (a GET, like the observation endpoint: it computes but is safe and idempotent; nothing is persisted).
`404` unknown id, `422` malformed id or unknown/repeated/out-of-range parameter, `409` verify when the record's earnings event
is no longer stored, `503` only when the database is unavailable, a generic `500` for a record that fails integrity
validation. There is no save, update or delete route: saving stays an internal service. The list summarises stored columns and
does not re-hash; integrity is checked on get and verify.

Startup validation is limited to concrete dangerous misconfiguration: a malformed or non-PostgreSQL `QF_DATABASE_URL`
fails at startup (without echoing the value) and `QF_DEBUG=true` is rejected when `QF_ENVIRONMENT=production`. No provider
key is required to start the API.

### Benchmark discovery API (Step 11.0B, read-only)

`GET /api/v1/benchmarks` lists the stored benchmarks a caller can explicitly select, so a client can obtain the `benchmark_id`
the research observation endpoint requires. Items carry identity only: `id`, `name`, `symbol` and `currency` (nullable, as
stored) and `is_active`. Active and inactive benchmarks are both listed. Order is `name` then `id` (names are not unique);
`limit` 1-200 (default 50) and `offset` are the only parameters (unknown or repeated -> `422`); the shape is
`{items, total, limit, offset}`; `503` only when the database is unavailable. It chooses, recommends and defaults nothing
(research still needs an explicit `benchmark_id`), and exposes no source, ingestion run, description, price level or return.
No detail, search, write or provider call.

### Research observation API (read-only)

`GET /api/v1/research/observations/{event_id}` serialises one 10.8A `ResearchObservation` (`observation/`): identity,
effective parameters, the stored event, an optional caller-supplied filing link, D0/D1 disclosure-evidence category,
accounting (the existing filing EPS contract, whole), a DESCRIPTIVE event study, a separate POINT_IN_TIME section
(`REFUSED` or `EVIDENCE_ELIGIBLE`), provenance and scoped refusals. Optional query: `horizons` (comma list, default
`1,5,20`), `benchmark_id`, `security_id`, `filing_accession` (requires `eps_period_class` and `eps_kind`; a filing is
never inferred). Every domain refusal is `200`; `404` only for an unknown event id; `422` for invalid/repeated/unknown
parameters; `503` only when the database is unavailable. Decimals are fixed-point strings; read-only, no provider call,
nothing persisted; no frontend.

### Filing EPS API (read-only)

`GET /api/v1/filings/{accession}/eps?period_class=QUARTER|ANNUAL&eps_kind=DILUTED|BASIC_AND_DILUTED` exposes, for one
accession, the existing chain **current EPS -> comparative EPS -> EPS change**. It is a transport surface: the router
(`api/v1/filing_eps.py`) composes `extract_comparative_eps_observation` + `compute_eps_change`, a pure presenter maps the
observations to the schema (`schemas/filing_eps.py`), and no financial rule lives in the API layer.

* **Contract:** both query parameters are required (no defaults, nothing inferred from the form); the accession must be a
  dashed EDGAR accession, else `422` (as for a bad/missing/extra parameter). Every valid request is **`200`, including domain
  refusals**: `NOT_INGESTED`, `MISSING`, `WRONG_UNIT`, `COMPARATIVE_PERIOD_MISMATCH`, ... are observations, not missing
  resources; `503` only when the database is unavailable. It reads stored facts only: no SEC/provider call, no write (the
  request session is rolled back), no cache, no migration. `GET /api/v1/filings` lists the accessions.
* **Registrant qualification (optional `cik`):** a filing's identity is `(company, accession)`, and a joint accession is stored
  once per registrant. `&cik=<ten-digit CIK>` (leading zeros kept; malformed is `422`) selects that registrant's filing; it is
  identity resolution only and never changes how EPS is selected. Without it the accession alone resolves exactly as before (a
  joint accession is `AMBIGUOUS`). With it, only that registrant's filing is read and a well-formed CIK matching no stored
  filing is `200` `NOT_INGESTED` echoing the requested CIK as `company_cik` (a **request echo, not evidence that a registrant
  was found**: such a refusal has `form: null` and no fact; never another registrant's data, never a `404`). A request that
  carries a CIK is never retried without it. A company with no stored CIK cannot be qualified, so ambiguity can remain there.
* **Response:** one nested envelope: `accession`, `company_cik`, `form`, `period_class`, `eps_kind`, `descriptive_only: true`,
  `no_point_in_time_claim: true`, `boundary_note`, then `current`, `comparative` and `change`, each with its own `status`
  (+ `reason` / `fact` evidence where the domain selected a fact: id, tag, taxonomy version, unit, period end, `qtrs`, value,
  `segments`, `coreg`). Every financial value is a **fixed-point string** (the Decimal's own exponent kept, never `1E-7`,
  never a JSON number). `percentage_change` is a **ratio** (`"0.5"` = +50%), only for a positive comparative; otherwise it is
  `null` with `percentage_unavailable_reason` while `eps_change` and `transition` stay available. The comparative is
  "comparative EPS as presented in this filing". No availability, acceptance or ingestion time is exposed.

```json
{"accession": "0000320193-24-000010", "company_cik": "0000320193", "form": "10-Q", "period_class": "QUARTER",
 "eps_kind": "DILUTED", "descriptive_only": true, "no_point_in_time_claim": true,
 "current": {"status": "AVAILABLE", "reason": "...", "fact": {"value": "1.5300", "unit": "USD", "qtrs": 1, "...": "..."}},
 "comparative": {"status": "AVAILABLE", "meaning": "comparative EPS as presented in this filing", "fact": {"value": "1.5200", "...": "..."}},
 "change": {"status": "AVAILABLE", "eps_change": "0.0100", "transition": "POSITIVE_INCREASED",
            "percentage_change": "0.00657894736842105263157894736842105263157894736842", "percentage_semantics": "ratio",
            "percentage_unavailable_reason": null}}
```

### Filings index and the descriptive accounting workspace (Step 10.6, frozen)

`GET /api/v1/filings?limit=&offset=&form=&cik=` is a bounded, deterministic, read-only index of **stored filings that have
at least one FSDS submission** (not all SEC filings, not "available" or "tradable" filings). A row is the stored filing
`(company, accession)`: `accession`, `company_cik`, `form`, `filing_date`, `report_date`, `fsds_period_end` (the period all of
the filing's submissions agree on, else `null`). Order is `filing_date DESC, accession DESC, cik DESC` (NULL CIKs last, then the row id); `limit` 50 (max 200),
`offset` max 1,000,000, exact `form` and `cik` filters (AND). `total` and the page are two reads, and offset paging can shift while
ingestion adds rows. No financial value, ticker, name, acceptance or ingestion time is exposed.

The frontend `/filings` and `/filings/[accession]` pages are server-rendered over these two APIs only (GET, stored data, no
provider call). The flow is **stored filing -> FSDS submission/facts -> filings index -> (registrant-qualified) detail -> current EPS
-> same-accession comparative -> descriptive change**. Parsers are strict (an unknown status, transition or reason is an
"unsupported response" error, never a generic fallback); financial values stay decimal strings end to end; a ratio is shown as an
exact string and as a rounded percentage (with an explicit "not zero" note when it rounds to 0.00%); transitions use neutral
wording; and every detail state carries the banner *"Descriptive accounting data from SEC FSDS. No point-in-time availability claim.
Not a trading signal."* A repeated query parameter is `422` on both APIs. The real backend JSON for a seeded scenario is pinned in
`apps/web/tests/contract/vertical.json` and checked by both the backend and the frontend suites.

Known limitations: a company without a stored CIK cannot be registrant-qualified, so a joint accession can remain `AMBIGUOUS`;
offset paging is not a snapshot; there is no browser end-to-end framework (the suites use in-process HTTP backend tests over real stored rows and rendered
page tests; a built-server check was run manually at the freeze, not automated); the FSDS `accepted` timezone is unresolved and FSDS is not point-in-time evidence; historical SEC
coverage is incomplete.

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
