# SEC acceptance-time investigation and repair design (step 11.2G)

Status: investigation and design only. No production source, migration or frontend change.
Base: `c0760ed` (11.2F evidence) on `616fb94` (11.2E). Observed evidence is kept apart from
inference; "double conversion" is deliberately not used because no SEC source establishes the
mechanism.

## 1. Question

`SecFiling.accepted_at` is filled from the submissions-JSON `acceptanceDateTime`, read as UTC
(10.7A rule). For the real NKE accession `0000320187-26-000037` the official EDGAR header says
`20260401161639` (16:16:39 Eastern = 20:16:39Z) while the JSON says `2026-04-02T00:16:39.000Z`, four
hours later. The adapter's tripwire refused the response and nothing was stored (fail-closed).

## 2. Official documentation (OBSERVED)

| Source | What it says |
|---|---|
| SEC "EDGAR Application Programming Interfaces" page (fetched 2026-10-07, sha256 `bd2c5142...9956`) | Describes `data.sec.gov/submissions/CIK##########.json` as "metadata such as current name, former name, and stock exchanges and ticker symbols"; says the JSON is updated in real time and a nightly bulk ZIP is republished at about 3:00 a.m. ET. **No definition of `acceptanceDateTime`, its format, timezone or precision.** |
| SEC "Overview of EDGAR APIs" PDF (22 pp., Dec 22, 2025) | Covers the filer-side submission/status APIs only. Nothing on `acceptanceDateTime`. |
| EDGAR Public Dissemination Service technical specification (cited by existing code and located by search; **not fetched in this run**) | Acceptance Time is "Time (EST) at which the submission was accepted by EDGAR", `HHMMSS`; header `<ACCEPTANCE-DATETIME>` is `YYYYMMDDHHMMSS`. |

Verdict: submissions-JSON `acceptanceDateTime` is **TIMEZONE UNDOCUMENTED** (and format and precision
undocumented). The header value is the only SEC-documented acceptance representation; the
"EST" label is read as Eastern wall clock with daylight saving (consistent with every header seen:
see section 4). Whether the header is the strongest source: it is the only documented one.

## 3. Reconstruction of the 10.7A decision (INFERENCE from retained evidence)

- The decision was explicitly empirical (mapper docstring: "not an official claim"). It did not rest on
  SEC documentation, and the `Z` suffix is ISO 8601 UTC notation, which made the reading the natural
  one.
- Evidence: a bounded live check on 2026-10-04 compared 11 accessions with their index/header values;
  11 of 11 matched to the second, with January/February and April/May dates, 7 pinned in
  `tests/test_sec_filings.py`.
- **Why it looked right then:** at that moment those JSON values really were header-consistent. Two of
  those pinned accessions are still consistent today (`0001045810-26-000021` NVDA,
  `0001193125-26-191507` MSFT).
- **What changed:** two other pinned accessions are NOT consistent today. The same accession now returns
  a different JSON value than on 2026-10-04:

  | Accession | 10.7A JSON (2026-10-04) | JSON today (2026-10-07) | Header (unchanged) |
  |---|---|---|---|
  | `0000320193-26-000006` | `2026-01-30T11:01:32.000Z` | `2026-01-30T16:01:32.000Z` (+5 h) | `20260130060132` |
  | `0000320193-26-000013` | `2026-05-01T10:01:00.000Z` | `2026-05-01T14:01:00.000Z` (+4 h) | `20260501060100` |

  (The header for `...-000006` was re-fetched in this run and is byte-for-byte consistent with the 10.7A
  pair.) So it was not a too-small sample or a coincidence; the **source drifted** for some registrants
  between two observations three days apart. The old tests pin the old captures and still pass because
  they are offline.

## 4. Comparison table (OBSERVED)

`diff` = (literal-Z instant) - (header read as Eastern, converted to UTC). `offset` = the Eastern UTC
offset for that date, computed from the zone database (not hard-coded).

| CIK | Accession | Form | Filed | JSON raw | Header raw | Header as UTC | diff | offset | diff / offset | Header fetched |
|---|---|---|---|---|---|---|---|---|---|---|
| 0000320187 | 0000320187-26-000037 | 10-Q | 2026-04-01 | 2026-04-02T00:16:39.000Z | 20260401161639 | 2026-04-01T20:16:39Z | +14400 | 14400 (EDT) | 1 | 11.2F |
| 0000320187 | 0000320187-25-000151 | 10-Q | 2025-12-30 | 2025-12-31T02:20:45.000Z | 20251230162045 | 2025-12-30T21:20:45Z | +18000 | 18000 (EST) | 1 | 11.2G |
| 0000320187 | 0001193125-20-084362 | 8-K | 2020-03-25 | 2020-03-25T16:44:41.000Z | 20200325084441 | 2020-03-25T12:44:41Z | +14400 | 14400 (EDT) | 1 | 11.2G |
| 0000320193 | 0000320193-26-000006 | 10-Q | 2026-01-30 | 2026-01-30T16:01:32.000Z | 20260130060132 | 2026-01-30T11:01:32Z | +18000 | 18000 (EST) | 1 | 11.2G |
| 0000320193 | 0000320193-18-000145 | 10-K | 2018-11-05 | 2018-11-05T18:01:40.000Z | 20181105080140 | 2018-11-05T13:01:40Z | +18000 | 18000 (EST) | 1 | 11.2G |
| 0000320193 | 0000320193-26-000013 | 10-Q | 2026-05-01 | 2026-05-01T14:01:00.000Z | 20260501060100 | 2026-05-01T10:01:00Z | +14400 | 14400 (EDT) | 1 | 10.7A pin (not re-fetched) |
| 0000789019 | 0001193125-26-027207 | 10-Q | 2026-01-28 | 2026-01-28T21:07:34.000Z | 20260128160734 | 2026-01-28T21:07:34Z | 0 | 18000 (EST) | 0 | 11.2G |
| 0000789019 | 0001193125-26-191507 | 10-Q | 2026-04-29 | 2026-04-29T20:06:24.000Z | 20260429160624 | 2026-04-29T20:06:24Z | 0 | 14400 (EDT) | 0 | 10.7A pin (not re-fetched) |
| 0001045810 | 0001045810-26-000021 | 10-K | 2026-02-25 | 2026-02-25T21:42:19.000Z | 20260225164219 | 2026-02-25T21:42:19Z | 0 | 18000 (EST) | 0 | 10.7A pin (not re-fetched) |

Across nine pairs the difference is **exactly 0 or exactly one Eastern offset for that date** (4 h in
EDT, 5 h in EST), never anything else. Discrepant pairs: 6 (3 NKE accessions, 3 AAPL accessions); consistent pairs: 3 (MSFT x2, NVDA). The same
accession can change between observations (section 3). Test fixture:
`apps/api/tests/sec_acceptance_evidence.json` (164 sanitised NKE rows plus these pairs) and
`tests/test_sec_acceptance_real_evidence.py`.

Response-level observations (OBSERVED): NKE, 164 supported rows: 160 trip the tripwire as read;
with one more offset removed all 164 are plausible, 126 at 16:00-16:30 ET. AAPL, 1000 recent rows:
literal reading plausible for only 12 of 78 self-filed rows. NVDA (82 of 82) and MSFT (80 of 81 with
the bundled agent filings) are literal-plausible. Plausibility is NOT a reliable discriminator: the NKE
8-K `0001193125-20-084362` is literal-plausible (12:44 ET) yet is 4 h off against its header
(true 08:44 ET), and the AAPL 06:01 ET filings read as 10:01-11:01 ET literally, which also looks
plausible. The tripwire only catches the shift where it happens to cross 17:30 or 22:00.

## 5. Hypotheses

| | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| H1 | JSON is genuine UTC | **Falsified as a global rule.** True for NVDA, MSFT and (on 2026-10-04) AAPL; false for 6 sampled accessions in NKE and AAPL | table |
| H2 | Eastern wall clock mislabelled `Z` | **Falsified.** Wall digits differ from the header digits in every discrepant pair (e.g. 00:16:39 vs 16:16:39) | table |
| H3 | An extra timezone conversion | **Consistent, not established.** Every discrepancy equals exactly one Eastern offset for its date (DST-aware, 2018-2026); the mechanism and where it happens are not documented | table, offsets from zone rules |
| H4 | Differs by era/source pipeline | **Partly.** Not era-based (AAPL shifted in 2018 and 2026; NKE 2018-2026). It differs by registrant document, and **by time** for the same accession (AAPL drifted 10-04 to 10-07). Filer-agent is not the discriminator (NKE and MSFT both contain Donnelley `0001193125` filings with opposite behaviour) | AAPL pins, NKE/MSFT |
| H5 | Recent vs historical differ | **No difference found, unverified.** The AAPL history file is a flat columnar object (no `filings.recent` wrapper, shape documented in the existing fixture); its rows show the same displaced pattern as AAPL recent (literal ET hours 4-5 for self-filed 10-K/10-Q), but no header was fetched for a historical row | `aapl-hist-001.json` |
| H6 | A documented SEC transformation | **Not found.** No SEC document defines the field | section 2 |

Winning statement (OBSERVED, not a mechanism): for the sampled accessions, the JSON value is either
the true acceptance instant or the true instant plus one Eastern offset; which one applies is a
property of the registrant's submissions document at the time of retrieval, not of form, era or filer
agent, and it can change. "Double conversion" is **not established**.

Remaining ambiguity: the mechanism; how many registrants are affected; whether SEC will fix or
regenerate (it already moved once in three days); whether `filingDate`, `reportDate` and `form` in the
same rows are affected (the NKE rows' filing dates are consistent with header-derived timing, no
discrepancy seen); whether history files have the same rule (not header-verified).

## 6. Request accounting

11 of 12 SEC requests were used (1 unused): API page (1), API overview PDF (1), submissions for AAPL,
NVDA, MSFT (3), headers for 5 accessions (5), AAPL history file (1). The 11.2F requests (2) are separate.
No other provider was called. Contact identity appears in no artifact. Ledger: `ledger-11.2G.json`
(gitignored artifacts).

## 7. What `accepted_at` is needed for (AUDIT)

- Required by: the `NOT NULL` column and `CanonicalSecFiling`; ordering of filings during ingest
  (`filings.py`, `history.py` sort by `(accepted_at, accession)`); a conflict check on re-ingest
  (a different `accepted_at` is a `SecFilingConflictError`); the `FactRow.filing_accepted_at` field.
- **Not used by**: filing identity (the key is `(company_id, accession_number)`), FSDS binding (by
  accession and CIK), the EPS selectors, the filing EPS API and filings index (no temporal field is
  exposed), research, signals, reproducibility, PIT. A repository-wide search finds no consumer of
  `filing_accepted_at` outside its own row type.
- Consequence A: the ingest conflict check turns SEC JSON drift into a hard failure. A filing stored
  from one retrieval would conflict on a later retrieval of the same accession.
- Consequence B: `accepted_at` is pure temporal provenance. Descriptive accounting does not need it.

## 8. Design decisions

**Source authority.** The header is the only documented and so far stable representation (the AAPL
header was identical on both dates). Options: A (JSON canonical) is rejected: undocumented and
observed to drift. B (header canonical for every filing) is correct but costs one request per
accession, an explosion for bulk identity (NKE recent: 1 + 164 requests; a historical backfill, 1000s).
D (both retained, agreement required) doubles the cost and refuses on a state the SEC itself changes.
**Recommended: C/E hybrid.** JSON stays the bulk source of filing *identity* (accession, form, filing
and report dates, primary document, items); `accepted_at` is populated only from the header,
fetched for a bounded, explicitly named set of accessions (for example those that FSDS facts are bound
to), and is otherwise NULL. A JSON value is never promoted to `accepted_at` without header
corroboration.

**Nullable `accepted_at`.** Recommended. Keeping NOT NULL forces either a fabricated or a per-filing
header request, or refusing valid filing identities that accounting does not need to be timed. Nullable
plus "NULL means not established" is the honest model and removes the SEC-drift conflict from re-ingest.
**MIGRATION REQUIRED** (one `ALTER COLUMN ... DROP NOT NULL`; no data to migrate: no `sec_filing` row
exists in the dev or validation database; integration databases are ephemeral).

**Acceptance basis column.** Not needed. With the rule "accepted_at comes only from the header", the
basis is implied and the run's raw artifact is the provenance. Add a basis column only if a second
source is ever admitted.

**Raw string preservation.** The submissions bytes are already retained content-addressed per run, so
the JSON text is auditable. A per-row verbatim text column is not recommended (the value is untrusted
and would invite reuse). The header text is retained as its own raw artifact (`sec-filing-header`
dataset) so the stored instant can be re-derived.

**Tripwire.** Purpose: detect schema drift, impossible chronology (acceptance far earlier than the
filing date; the existing `_require_not_implausibly_early`), date-only placeholders, malformed values
and, as a diagnostic only, a systematic shift when a JSON-vs-header comparison exists. It must NOT
treat hour-of-day distributions (including the 16:00-16:30 cluster) as correctness invariants, and must
not decide the timezone: it cannot (section 4). With `accepted_at` no longer derived from JSON, the
timezone-sensitive refusal can be retired; it should be kept as a logged warning counter.

**Granularity.** Whole-response refusal remains for schema/format drift and systematic placeholder
patterns (they invalidate the response). Per-row refusal for malformed/placeholder rows. Filing
identity is stored even when `accepted_at` is NULL. Per-row acceptance of JSON instants is rejected
(a systematic shift would pass row by row).

**Repair options.**
- A (reinterpret JSON by a stable rule): **rejected.** The rule is not stable (varies by registrant and
  by retrieval time) and cannot be detected from the row.
- B (header authoritative): **accepted for `accepted_at`**, on demand and bounded, not per row.
- C (identity without instant): **accepted** (needs the migration).
- D (hybrid): **recommended** = B + C.

**Hard invariants.** (1) `SEC_FILING_ACCEPTANCE` stays out of `APPROVED_AVAILABILITY_BASES`; (2)
`accepted_at` never flows to `observed_at`, `known_as_of`, BMO/AMC or execution eligibility; (3)
FSDS `accepted` text stays verbatim and unparsed and is not touched; (4) no fabricated instant; (5)
a header discrepancy with a stored value is a conflict, never an overwrite.

## 9. Existing data

No `sec_filing` or FSDS row exists in the dev or validation databases (queried read-only). No stored
data was produced by the questioned rule; no remediation. The encoded assumption lives in the mapper
(`parse_submissions_acceptance_datetime` and its docstring), the seven pinned pairs in
`tests/test_sec_filings.py` (historical captures; two no longer match the live service), the fixture
`tests/sec_history_fixture.py` (synthetic rows with `Z` instants), and tests in `test_sec_client.py`,
`test_sec_history.py` and the integration ingestion tests.

## 10. Minimum condition to resume the NKE accounting golden run

A `sec_filing` row for `0000320187-26-000037` (identity from the submissions JSON) with
`accepted_at` NULL, or populated from its header (1 request). Then FSDS binding by accession and CIK,
selectors, API and index run as designed. Nothing temporal is required.

## 11. Proposed implementation (not done)

1. Migration 0014: `sec_filing.accepted_at` nullable. Models, `CanonicalSecFiling.accepted_at:
   datetime | None`, repository `FactRow.filing_accepted_at` nullable (unused downstream).
2. Mapper: stop deriving an instant from the JSON; keep format, placeholder and implausibly-early
   validation as quality checks; drop the timezone-sensitive systematic refusal (keep a counter).
   Ordering uses `(filing_date, accession)`.
3. Re-ingest/conflict: compare identity fields only; `accepted_at` NULL-to-value is allowed once, from
   the header; a different non-NULL value is a conflict.
4. New bounded command `sec-filing-acceptance --accession ...` (max N, one request each, header only,
   dataset `sec-filing-header`, raw retained); parses `YYYYMMDDHHMMSS` as `America/New_York`; rejects a
   header whose accession or date disagrees.
5. Tests: DST and standard time; multiple registrants and forms; recent and history shapes; placeholder,
   malformed, early and discrepant values; tripwire behaviour; raw preservation; identity stored with
   NULL; FSDS binding with NULL; the PIT invariants (`SEC_FILING_ACCEPTANCE` unapproved, `accepted_at`
   absent from observed_at/known_as_of); invert `test_the_real_nke_response_is_currently_refused...`
   so the real NKE fixture stores 164 identities with NULL instants.
6. Docs; mapper docstring rewritten (history of the 10.7A rule and its drift).
Estimated size: small-medium; one migration; no frontend change (no API field exposes the instant).

## 12. Readiness

The design is ready for review. Open item for the reviewer: whether header corroboration should also
be offered as an opt-in for every ingested accession (cost 1 request each) or stay on-demand only.
