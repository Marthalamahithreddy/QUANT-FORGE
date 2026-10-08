# QuantForge Frontend V2 Design Specification

| | |
|---|---|
| Step | 11.1A (design and architecture only) |
| Status | **Approved at the Step 11.1A checkpoint.** Implementation proceeds from Step 11.1B. |
| Backend base | `c7257a65ef65c3ec37ce0e20dcd9cef6c9f23cab` (Step 11.0 COMPLETE, migration head `0013`) |
| Frontend base | `apps/web`, Next.js 16.3.6, React 19, Tailwind 4, Vitest 5 (313 tests at the audit) |
| Audience | The implementers of Steps 11.1B to 11.1F, and the reviewer who approves this design |

**Approval record.** Step 11.1A was approved on human review. Decision **D1** (how research baselines get saved) is
**deferred**: it is not solved in 11.1B, no public `POST`, CLI or authentication work is part of the foundations, and
Saved research stays viewer-first. D1 must be decided before Step 11.1E.

This document is the source of truth for Frontend V2. It was derived from the code that exists
(frontend source, the generated OpenAPI of the backend at the base above, and the Next.js 16 docs shipped in
`node_modules/next/dist/docs`), not from screenshots or mockups. Every contrast ratio quoted was computed, not
estimated. No production source, backend source, migration, dependency or test was changed to produce it.

---

## Table of contents

1. Product definition
2. Audit of the current frontend
3. Audit of the backend contract (OpenAPI)
4. Domain honesty: the state system
5. Information architecture and routes
6. Application shell
7. Research context, URL state and client state
8. Surface designs: Overview, Companies, Earnings, Filings, Research, Saved research
9. Provenance, reproducibility and verification
10. Visual system: numbers, typography, tokens, density, cards, tables, charts, motion
11. Quality: loading, empty and error states, accessibility, responsive strategy
12. Engineering: API client, runtime validation, components, themes, tests, performance, dependencies
13. Backend to UI contract matrix
14. Backend gaps seen from the UI
15. Implementation plan (slices 11.1B to 11.1F)
16. Design review answers, open decisions and risks

Appendices: A endpoint contracts, B status to state mapping, C copy deck, D URL parameters, E audit findings.

---

## 1. Product definition

QuantForge is a **dense, evidence-oriented financial research workspace** for examining earnings events,
accounting evidence, filings, market reactions, benchmark-relative performance and reproducibility.

It is not a trading terminal, a portfolio tracker, a signal or recommendation product, an AI chat surface or a
generic SaaS dashboard. It never says buy, sell, bullish or bearish, and it never ranks or scores anything.

Every screen must let a reader answer six questions without leaving the page:

1. **What happened?** (the stored observation, the measured returns)
2. **What evidence supports it?** (source, filing, run, digest, fingerprint)
3. **What could be computed?**
4. **What could not be computed, and is that a refusal, a gap or something the reader never asked for?**
5. **Why?** (the backend's own reason string, shown verbatim)
6. **How reproducible is it?** (COMPLETE or PARTIAL, with the gaps named)

### 1.1 Primary user and workflow

The user is an individual research analyst running a local workstation. There is no authentication and no
multi-user concept in the product today.

The primary workflow:

```
Find an event  ->  read its stored evidence  ->  configure a study  ->  read the descriptive result
   (Earnings)        (Event record)             (Research workspace)     and what was refused
                                                                              |
                                                          inspect provenance  ->  compare with a saved baseline
                                                                              (Evidence drawer)   (Saved research)
```

### 1.2 Default landing page

`/` (Overview) is a **launchpad**, not a KPI dashboard: system readiness, the newest stored earnings events, the
newest filings, the newest saved baselines, a ticker entry point, and an honest "coverage and limits" note. Every
number on it is a `total` or a row returned by an existing list endpoint. Nothing is aggregated on the client.

### 1.3 What is deliberately not in Frontend V2

A Companies directory, fundamentals or valuation cards, backtests, signals, a save action for research baselines,
price-history charts, intraday charts, any aggregate KPI that needs client-side crawling, and any mock data.
Each is either blocked by missing backend data (section 14) or out of product scope.

---

## 2. Audit of the current frontend

Everything below was read directly from `apps/web`.

### 2.1 What exists

| Area | Reality |
|---|---|
| Routes | `/` (Overview), `/earnings`, `/earnings/[id]`, `/filings`, `/filings/[accession]`, `/research` (static text page). `error.tsx`, `earnings/not-found.tsx`. No `loading.tsx`, no `global-error`, no root `not-found`. |
| Rendering | Every page is an async **server component**. All fetching is server-side (`fetch` with `cache: "no-store"`, 4000 ms timeout, `AbortSignal.any`). There is no browser data fetching, so CORS never matters to the UI. |
| Forms | Plain `method="get"` forms; URL is the state; works without JavaScript. |
| Client components | `Sidebar` (active route), `EarningsTable` (local sort and filter) and the `error.tsx` boundary. Nothing else. |
| Styling | Tailwind 4 `@theme` tokens in `globals.css`: surfaces 0 to 3, two border tones, three text tones, one accent, three status colours. **Dark only** (`color-scheme: dark`). System font stacks. |
| API layer | `lib/api/client.ts` (typed `fetch`, `ApiError`), `earnings.ts`, `filings.ts`, `health.ts`; hand-written strict parsers in `types/earnings.ts`, `types/filings.ts`, `types/api.ts`; exact-decimal formatting with BigInt in `lib/format.ts`. |
| Domain rules already encoded | Decimals never become `number`; instants render from the ISO string, never through `Date`; unknown enum values are contract failures; neutral wording (no good/bad framing); the filing EPS page never falls back from one EPS kind to another; `As presented in this filing` is shown for comparative EPS; a ratio that rounds to 0.00% is flagged as non-zero. |
| Tests | 13 files, 313 tests (Vitest, jsdom, React Testing Library, a backend-pinned `contract/vertical.json`). No browser E2E, no accessibility automation, no visual tests. |
| Dependencies | `next`, `react`, `react-dom` only at runtime. No chart, icon, table, date or state library. |
| Build baseline | Production build emits 12 client chunks, about **190 KB gzip in total across all routes**, mostly framework. Six routes, all dynamic. |

### 2.2 Strengths to keep

- The honesty culture: explicit states, no fabricated values, backend classifications displayed rather than recomputed.
- Server-first rendering with URL state; no client store.
- Strict, exact-string decimal handling and its tests.
- The vocabulary and wording maps in `filings-view.ts` and `availability.ts` (neutral, complete, test-guarded).
- A skip link, landmark structure, captioned tables, labelled controls and scrollable regions that are keyboard-focusable.
- `prefers-reduced-motion` already honoured globally.

### 2.3 Defects and gaps found (none is fixed by this step)

Each finding has a file reference and is addressed by a named slice.

| # | Finding | Evidence | Addressed in |
|---|---|---|---|
| F1 | **No light theme**; `color-scheme: dark` is hard-coded. | `app/globals.css` | 11.1B |
| F2 | **Form control borders fail WCAG 1.4.11 non-text contrast.** `border-strong` `#2d3743` is 1.60:1 against `#0a0e13` and 1.43:1 against the `#151b24` input fill; the requirement is 3:1. | measured | 11.1B (new `--border-control`) |
| F3 | **Global `font-feature-settings: "tnum"`** on `body` makes every proportional text run tabular; `cv02/cv03/cv04` are Inter features applied to a system stack that has none of them. | `globals.css` | 11.1B |
| F4 | **Two incompatible result taxonomies.** `Result<T>` (ok / not_found / error) for earnings; `FilingsResult<T>` (ok / unavailable / rejected / contract / error) for filings. The earnings client shows a 503 as a generic error. | `lib/api/earnings.ts`, `filings.ts` | 11.1C |
| F5 | **Error text exposes the backend URL.** `describe(error)` returns the `ApiError` message, which embeds the full request URL, and pages render it. | `lib/api/client.ts`, `app/earnings/page.tsx` | 11.1B |
| F6 | **No loading states.** No `loading.tsx` and no `Suspense`; every page awaits all fetches (including a health call for the top bar) before painting anything. | all pages | 11.1B / each slice |
| F7 | **Stale product copy.** The Research page and Overview say event-study results "are not exposed by the API yet"; the web README says the app is "a single page". The research endpoint, records and benchmarks now exist. | `app/research/page.tsx`, `app/page.tsx`, `README.md` | 11.1B / 11.1D |
| F8 | **Page-local sort and filter** in `EarningsTable` can be mistaken for result ordering; the table says so in a caption but the control looks global. | `earnings-table.tsx` | 11.1C (decision D3) |
| F9 | **Navigation lists "Planned" rows** (Companies, Backtests) that add noise and a permanent "Sections marked planned are not implemented" note. | `lib/navigation.ts`, `sidebar.tsx` | 11.1B (decision D4) |
| F10 | **Brand shown twice** (rail "QF/ Workspace" and top bar "QuantForge") and the top bar spends a full row on identity. | `sidebar.tsx`, `topbar.tsx` | 11.1B |
| F11 | `filings.ts` imports `PAGE_SIZE` from `earnings.ts`; `Badge` tone names (`positive`, `caution`, `negative`) mix status and financial-direction vocabularies. | code | 11.1B / 11.1C |
| F12 | Request timeout is a single 4 s default; a research study (about a dozen queries) and a verification (about ten) need a per-call budget. | `client.ts` | 11.1C |
| F13 | No `loading`/`not-found` for most segments, page `metadata` is a fixed title per page rather than a template. | `app/*` | 11.1B |
| F14 | The Next.js error boundary uses the `retry` prop. **This is correct for Next 16.2+** (`unstable_retry` was added in 16.2.0 and documented as `retry`); noted so nobody "fixes" it back to `reset`. | `app/error.tsx`, Next docs | none |

### 2.4 Platform facts that shape the design (from the shipped Next 16 docs)

- `cacheComponents` and `partialPrefetching` are not enabled. Pages are dynamic and always read live data. This design does **not** adopt them: research data is a live database read, and instant-navigation machinery is an optimisation that can come later.
- A page-level `loading.tsx` wraps the segment in `Suspense` but a `searchParams`-only navigation reuses the same segment, so the old UI stays during the transition. The research workspace therefore keys its result region with `<Suspense key={canonicalQuery}>` so a changed configuration shows a skeleton. This must be verified in the first prototype of 11.1D (risk R3).
- Client components can receive server-rendered children, so the evidence drawer can wrap server content without client data fetching.

---

## 3. Audit of the backend contract (OpenAPI)

The generated OpenAPI at the base commit has **12 paths and 81 schemas**. All are `GET`. There is no write verb.
Full contracts are in Appendix A; the inventory:

| Endpoint | Used by | Notes |
|---|---|---|
| `GET /health`, `/api/v1/health` | shell status | liveness only |
| `GET /api/v1/health/ready` | Overview | `503` with a structured body when a dependency is down |
| `GET /api/v1/earnings-events` | Earnings, Overview, Research finder, Saved research (enrichment) | `limit<=200`, `offset`, `ticker`, `date_from`, `date_to`; newest event date first |
| `GET /api/v1/earnings-events/{event_id}` | Event record, Research header, Saved research (enrichment) | `404`, `422` |
| `GET /api/v1/filings` | Filings, Overview, Research filing picker | `limit<=200`, `offset`, `form`, `cik` (exact) |
| `GET /api/v1/filings/{accession}/eps` | Filing detail | `period_class` and `eps_kind` required; `cik` optional |
| `GET /api/v1/benchmarks` | Research benchmark selector, Saved research (labels) | `limit<=200`, `offset`; identity fields only |
| `GET /api/v1/research/observations/{event_id}` | Research workspace, Event record (preview) | `horizons`, `benchmark_id`, `security_id`, `filing_accession` (+ both EPS selectors) |
| `GET /api/v1/research/records` | Saved research list, Overview | `limit<=200`, `offset` |
| `GET /api/v1/research/records/{record_id}` | Saved research detail | parsed manifest and result |
| `GET /api/v1/research/records/{record_id}/verify` | Saved research verification | read-only rerun, nothing persisted |

Conventions the UI relies on and must mirror:

- **Pagination**: `limit` 1 to 200 (default 50), `offset` up to 1,000,000, response `{items, total, limit, offset}`; unknown or repeated parameters are `422`.
- **Errors**: FastAPI `detail`. `503` is always the generic `{"detail": "Database unavailable."}`. `500` is generic. Record integrity failure is the fixed `Stored research record failed integrity validation.`
- **Decimals** are fixed-point **strings** everywhere (`DecimalText`), never JSON numbers.
- **Observation response** always contains `descriptive.study`; `point_in_time.study` is `null` when the PIT state is `REFUSED`. Accounting reuses the filing EPS contract whole.
- **No clock** appears in any response except `created_at` (record save time, persistence metadata), `ingested_at` (event record), `observed_at` and event instants.

---

## 4. Domain honesty: the state system

The interface must **never** collapse these into one appearance: missing, unknown, not requested, not applicable,
refused, partial, ambiguous, unavailable, or an application error. They are materially different statements.

### 4.1 The ten states

| State | Means | Glyph (inline SVG, 12 px, `aria-hidden`) | Border / fill | Text label | Announced as |
|---|---|---|---|---|---|
| `VALUE` | A value exists. | none | none | the value | the value |
| `NOT_STATED` | The source supplied nothing (null from source). Not an error, not zero. | `-` (en dash) | none, muted text | "Not stated" | "Not stated by the source" |
| `NOT_REQUESTED` | The reader did not ask (no benchmark chosen, no filing linked). | dotted ring | **dotted** outline | "Not requested" + the action that would request it | "Not requested" |
| `NOT_APPLICABLE` | The concept does not apply. | none | none, muted | "Not applicable" | "Not applicable" |
| `MISSING` | Evidence the engine needs is absent from stored data. | hollow circle | solid amber-tint outline | "Missing" + reason | "Missing: reason" |
| `UNAVAILABLE` | Cannot be derived because an input was unavailable. | open diamond | solid neutral-strong outline | "Unavailable" + "because" line | "Unavailable because: reason" |
| `REFUSED` | The engine declined by design (a guard, a policy). | circle-slash | **dashed** neutral-strong outline | "Refused" + reason | "Refused: reason" |
| `AMBIGUOUS` | More than one candidate; the engine will not choose. | branch | solid info-tint outline | "Ambiguous" + candidates note | "Ambiguous: reason" |
| `PARTIAL` | Some parts available, some not. | half-filled circle | solid amber-tint outline | "Partial" | "Partial" |
| `ERROR` | The application or transport failed (HTTP, network, contract). | triangle | solid danger outline, `role="alert"` | "Error" + safe message | alert |

Rules:

1. **Text always carries the meaning.** Colour, glyph and border style are redundant cues. Nothing relies on colour alone.
2. Every state except `VALUE` renders the backend's own `reason` string verbatim where one exists. The UI does not paraphrase a reason.
3. **Domain states are never `ERROR`.** A refusal uses `role="status"` or a labelled region, never `role="alert"`, never the danger colour, and never the global error boundary.
4. `NOT_REQUESTED` is the only state that carries a call to action ("Select a benchmark"), and the action is a link that edits the URL.
5. In a dense table a non-`VALUE` cell shows the glyph, a one- or two-word label ("Not stated", "Refused") and exposes the full reason through an accessible name and a hover/focus hint. A bare dash is allowed only for `NOT_STATED`.
6. `0`, `0.00%`, an empty string and an em dash are never used to stand in for any state.

### 4.2 Section-level treatment

- **Inline** (a cell or a labelled value): glyph + short label, as above.
- **Section** (`RefusalNotice`): a callout with a left rule in the state's border style, a title naming the section, the state label, the reason, and, where there is one, the next action. It replaces the section body; it does not hide sibling sections.
- **Ledger**: the observation's `refusals[]` list is also shown once, grouped by `scope`, so a reader can see everything that was not computed in one place (section 8.5.4).

### 4.3 Mapping from backend statuses

Appendix B maps every status enum in the OpenAPI to a state. It is implemented as exhaustive
`Record<Status, StateKind>` tables, with a unit test that fails if the committed OpenAPI snapshot gains an enum
member without a mapping (section 12.4).

### 4.4 Severity

Domain states carry at most **caution** severity. Only `ERROR` carries **alert** severity. This is what keeps a
point-in-time refusal from ever looking like a failure.

---

## 5. Information architecture and routes

### 5.1 Principles

1. Routes follow **identities the backend actually exposes**: an earnings event id (UUID), a filing accession (+ optional CIK), a record id (UUID). There is no company id route because no endpoint accepts one.
2. Research is a top-level concept, not a tab inside an event; an event is its input.
3. Every configurable input of a study lives in the URL using the backend's own parameter names, so a copied URL is a reproducible request.
4. No page invents a relationship the backend did not state. A filing is linked to a study only when the reader chooses it.

### 5.2 Navigation hierarchy

```
Overview
Evidence
  Earnings
  Filings
Research
  Research            (event finder and workspace)
  Saved research
```

Companies is **not** in the navigation in 11.1 (decision D4). Backtests is not in the navigation. The Overview
page carries one honest "Not available yet" note naming both. Company identity is reachable as context links
from events and filings (section 8.2).

### 5.3 Route map

| Route | Purpose | Backend identity | Query parameters (all optional unless stated) |
|---|---|---|---|
| `/` | Overview launchpad | none | none |
| `/earnings` | Earnings list | none | `ticker`, `date_from`, `date_to`, `page` |
| `/earnings/[eventId]` | **Event record**: the stored observation, its availability evidence and listing history | event UUID | none |
| `/filings` | Filings index | none | `form`, `cik`, `page` |
| `/filings/[accession]` | Filing accounting view (EPS) | accession | `cik`, `period_class`, `eps_kind` |
| `/research` | Research entry: event finder, explanation | none | `ticker`, `date_from`, `date_to`, `page` |
| `/research/events/[eventId]` | **Research workspace** for one event | event UUID | `benchmark_id`, `horizons`, `filing_accession`, `eps_period_class`, `eps_kind` |
| `/research/saved` | Saved baselines list | none | `page` |
| `/research/saved/[recordId]` | One saved baseline; verification | record UUID | `verify=1` |

Why `/research/events/[eventId]` and not `/earnings/[id]/research`: the study is the product's central object and
deserves its own top-level section; the event record page is the evidence, the workspace is the computation.
The two are cross-linked in both directions.

Reserved for later, **not created**: `/companies`, `/companies/[cik]`. No stub routes, no placeholder pages.

### 5.4 Cross-navigation

| From | To | How |
|---|---|---|
| Earnings list row | Event record | date link (primary) |
| Earnings list row | Research workspace | "Research" action link |
| Event record | Research workspace | primary action |
| Event record | `/earnings?ticker=T` | "Earnings for T" (ticker valid on the event date) |
| Event record | `/filings?cik=C` | "Filings for CIK C" (only when a CIK exists) |
| Workspace | Event record | breadcrumb and context bar link |
| Workspace filing picker | `/filings/[accession]?cik&period_class&eps_kind` | "Open filing view" |
| Filing list row | Filing view | accession link (carries `cik` and the visible, changeable initial selectors, as today) |
| Saved record | Research workspace | "Re-run this request live": rebuilds the URL from the record's request. An explicit link, never hidden state |
| Saved record | Event record | event link |
| Overview panels | their lists and details | row links |

### 5.5 Breadcrumbs

Shown on detail routes only, in the header strip: `Earnings / FY2026 Q1 · Example Co`, `Filings / 0000320193-24-000010`,
`Research / Example Co · 2026-04-30`, `Saved research / 7c1b6f0e`. List pages show their title only. Breadcrumbs are
a navigation landmark (`nav aria-label="Breadcrumb"`, `aria-current="page"` on the last item).

### 5.6 Page titles

A `title.template` of `%s · QuantForge` with a specific title per route, for example `Research · Example Co 2026-04-30`.
This fixes the current fixed-per-page titles (F13).

---

## 6. Application shell

Desktop-first, information-dense, minimal chrome. Vertical space is the scarcest resource.

### 6.1 Structure

```
+-------------+----------------------------------------------------------------+
| QF/         | Breadcrumb or page context           [theme] [env] (o) Connected |  <- 44 px header strip
| Overview    +----------------------------------------------------------------+
| ----------- |                                                                |
| EVIDENCE    |  page content                                                  |
|  Earnings   |                                                                |
|  Filings    |                                                                |
| RESEARCH    |                                                                |
|  Research   |                                                                |
|  Saved      |                                                                |
+-------------+----------------------------------------------------------------+
   12.5 rem rail
```

| Element | Decision |
|---|---|
| Navigation | Left rail, fixed 12.5 rem, **not collapsible** in V2 (a collapsed rail needs an icon system that earns nothing here). Groups labelled "Evidence" and "Research" in the section-label style. |
| Brand | Single wordmark `QF/` (mono, accent slash) at the top of the rail. The top bar no longer repeats the product name (F10). |
| Header strip | 44 px: breadcrumbs / page context at left; at right the theme control, the environment chip and the API status. Replaces the current two-row chrome. |
| API status | A small dot plus text ("Connected" / "Unavailable"), text always present. Resolved in a `Suspense` boundary so it never delays page content (F6). Detail (service, version, environment) in the accessible name and a hover/focus hint. |
| Environment chip | Shown only when the environment is not `production`. |
| Page width | Three container classes: **reading** `64rem` (detail pages, prose), **workspace** `96rem` (research), **table** fluid up to `120rem` (lists). Page padding 16 px (24 px from `sm`). |
| Contextual actions | Live in the `PageHeader` actions slot, right-aligned (for example "Open research workspace"). |
| Skip link | Kept (`#main-content`). |
| Responsive collapse | below `1024px` the rail becomes a menu button in the header strip opening a modal drawer (focus trapped, Esc closes). |
| Planned items | Not rendered (decision D4). |

### 6.2 Breakpoints

| Name | Width | Behaviour |
|---|---|---|
| Large desktop | `>= 1536px` | Research workspace shows an **inspector** docked at the right (evidence drawer becomes a docked panel); tables show all columns. |
| Standard laptop | `1280 to 1535px` | Rail + content; research = config rail + results; evidence drawer overlays. |
| Tablet | `768 to 1279px` | Rail becomes the menu drawer; research configuration collapses into a disclosure above the results; secondary table columns hidden. |
| Mobile | `< 768px` | Single column; controls 44 px tall; tables scroll horizontally with a sticky identity column; evidence drawer is a full-height sheet; research sections stack with an in-page section index. |

Primary target is desktop and laptop. Mobile is usable for reading and light navigation, not feature-identical in density.

---

## 7. Research context, URL state and client state

### 7.1 The context model

| Dimension | Where it lives | Selectable? | Notes |
|---|---|---|---|
| Event | route (`/research/events/[eventId]`) | by choosing the route | never inferred from a company |
| Company | derived from the event | no | shown in the context bar; no company route exists |
| Security / listing | derived from the event | **no** | the API offers `security_id` as a request knob but exposes no way to discover one (gap N3); ambiguity is shown as `AMBIGUOUS` |
| **Benchmark** | URL `benchmark_id` | **explicit only** | never defaulted, never remembered, never recommended |
| Price basis | fixed `SPLIT_ADJUSTED` | no | displayed read-only with the backend's `price_basis_note` |
| Horizons | URL `horizons` | yes | comma list; empty means the research default `1,5,20`, shown as "default" |
| Filing | URL `filing_accession` | **explicit only** | picked from the company's stored filings or typed; never inferred |
| Period class, EPS kind | URL `eps_period_class`, `eps_kind` | explicit, **required together with a filing** | never inferred from the form type; the filing list's pre-selection is a visible, changeable convenience, as today |
| Mode | not selectable | no | descriptive and point-in-time are **both always shown** as separate sections |

**There is no global research context.** Nothing is carried silently between pages. Context travels only through
links the reader can see and follow (for example "Open with the same benchmark" builds a URL containing
`benchmark_id`).

### 7.2 What belongs where

| Kind of state | Mechanism | Examples |
|---|---|---|
| **URL state** | path + query, canonicalised | filters, page, event, benchmark, horizons, filing, selectors, `verify=1` |
| **Server-fetch state** | server components, `no-store` | every API result |
| **Ephemeral UI state** | React state in small client components | evidence drawer open/closed, copy confirmation, a `<details>` open state, unsent form edits |
| **Persistent preferences** | a cookie read by the server layout (no flash) | theme (`system` / `light` / `dark`), density (`compact` / `comfortable`), direction tint (on/off) |

No Redux, Zustand, React Query or global context is introduced. `localStorage` is not used for any functional state.
A benchmark, filing or EPS selection is **never** stored as a preference.

### 7.3 URL rules

- Parameter names mirror the backend (`benchmark_id`, `horizons`, `filing_accession`, `eps_period_class`, `eps_kind`, `ticker`, `date_from`, `date_to`, `form`, `cik`) so a URL can be pasted into an API call.
- Defaults are **omitted** from the URL; the UI labels them "default".
- Parsing is strict and local: a repeated parameter, an unsupported enum value, a malformed id, a bad date or an invalid horizon list produces a local `ERROR` panel **before** any request and keeps what the reader typed in the form (the existing filings page already does this and keeps doing so). Nothing is coerced or repaired.
- Forms are `method="get"`; "Apply" is explicit. A study is never recomputed on every keystroke.
- The URL never contains fingerprints, source ids, run ids, credentials or any provenance data. Record ids and event ids are public identifiers already returned by the API.
- Evidence drawer state is not in the URL.

---

## 8. Surface designs

### 8.1 Overview (`/`)

**Purpose**: orient and launch. No KPIs beyond what list endpoints return.

| Panel | Source | Content | Failure behaviour |
|---|---|---|---|
| System | `/health`, `/health/ready` | API process, database, Redis, each as text + dot ("Reachable"/"Unavailable"); API version | the panel shows `ERROR`; other panels are unaffected |
| Newest earnings events | `/earnings-events?limit=8` | 8 compact rows: event date, company, ticker at event, reported EPS, PIT status; header shows `N stored` from `total`; link "All earnings" | per-panel `Suspense`; 503 shown as unavailable, not as empty |
| Newest filings | `/filings?limit=5` | form, filing date, CIK, accession; `N stored` | same |
| Saved research | `/research/records?limit=5` | saved time, level badge, short fingerprint; `N saved` | same; empty shows the honest empty state of section 11.2 |
| Start research | none | one ticker field that submits to `/earnings?ticker=` (GET form), plus a link to `/research` | none |
| Coverage and limits | static text | what is and is not stored or available: not every SEC filing, price history comes from one provider, Companies and Backtests not available yet, no baselines can be saved from the UI yet | static |

Rules: the five reads run in parallel and stream independently; there is **no** "latest event date incl. scheduled"
metric, no counts derived by looping pages, no sparkline, no gauge. "Newest" is by the API's own order (event date
descending for events, filing date descending for filings, save time descending for baselines), and the panel
heading says so.

### 8.2 Companies

**Decision (smallest honest solution, option C-lite): no Companies page and no company routes in 11.1.**

The backend has no company endpoint and the earnings payload exposes a company id that no endpoint accepts. A
directory built by crawling events would be an invented, incomplete list presented as complete. So:

- **Company identity is a context, not a place.** Wherever a company appears (event record, earnings rows, research context bar, saved record) it is shown as `display name` plus `CIK` plus the **tickers valid on the event date** (the API's own `valid_on_event_date`).
- Two **context links** carry the reader to existing, honest lists: `Earnings for <ticker>` (`/earnings?ticker=T`) and `Filings for CIK <cik>` (`/filings?cik=C`). Neither is shown when its identifier is absent.
- `/companies/[cik]` is reserved in the route map for the day an endpoint exists.

#### 8.2.1 Conceptual company workspace (what it would be, and what is supportable today)

| Section | Supportable today? | How |
|---|---|---|
| Overview | **No** (no company endpoint) | would need legal name, identifiers, listing history; only partially visible from an event |
| Earnings | Yes, by ticker | `/earnings?ticker=T` (the ticker filter is proven on the event date) |
| Filings | Yes, by CIK | `/filings?cik=C` |
| Research | Per event only | via the earnings list |
| Saved research | **No** per company | the record list has no company or event filter (gap P2) |
| Fundamentals, valuation, price history | **No** and not planned | no invented cards |

### 8.3 Earnings

#### 8.3.1 Earnings list (`/earnings`)

A dense, server-filtered table. Rows are **stored observations**, not de-duplicated events; a revision or a second
source for the same fiscal period is its own row (the existing note stays).

| Column | Field | Tier | Treatment |
|---|---|---|---|
| Event date | `event_date` + `fiscal_year/quarter` | P1, sticky | link to the event record; fiscal period beneath |
| Company | `company.display_name`, `cik` | P1 | CIK beneath, mono |
| Ticker | listings where `valid_on_event_date` | P1 | `Not resolved` state when none (never a current ticker) |
| Reported EPS | `reported_eps` | P1 | exact string, right-aligned; `NOT_STATED` when null |
| Est. EPS | `estimated_eps` | P1 | same |
| EPS surprise | `eps_surprise` | P1 | signed exact absolute, ratio as percent beneath; non-`AVAILABLE` statuses use the state system with the status reason |
| PIT status | `availability.pit_eligibility` | P1 | state mark: `ELIGIBLE` is the **PIT** hue, ineligible variants name their reason in short form; never green |
| Timing | `timing` | P2 | "Before open", "After close", "During session", "Not stated" |
| Observed at | `availability.observed_at` | P3 | UTC instant, or "Not disclosed" |
| Basis | `availability.observed_at_basis` | P3 | "Source publication" / "SEC filing acceptance" (never described as a release time) |
| Source | `source.name` | P3 | text |
| Research | link | P1 | "Research" -> `/research/events/[id]` |

Not shown (fields exist but earn no column): `currency`, revenue, `ingested_at`, `legal_name`, listing metadata
(all on the event record).

Filters: ticker, from, to, as server-side GET form fields with active-filter chips and "Clear". **No local sort or
filter** (decision D3): the API order (event date descending) is the contract, and a page-local re-sort that reads as
a global one misleads. Pagination: the shared pagination control (section 10.7).

#### 8.3.2 Event record (`/earnings/[eventId]`)

Layout (reading width, two columns from `xl`): evidence on the left, context on the right.

| Block | Content |
|---|---|
| Header | company, `FY.. Q..`, event date, timing; actions: **Open research workspace** (primary), `Earnings for T`, `Filings for CIK` |
| Stored observation (descriptive frame) | reported/estimated EPS, currency, revenue, EPS surprise with its status and reason, event date, event instant ("Source gave a date only" when null), timing "as stated by the source" |
| Availability (point-in-time frame, visually separate) | observed at, evidence basis with its meaning ("It is not the earnings release time and does not prove first public disclosure" for SEC acceptance), the backend's PIT verdict and reason |
| Market reaction (preview) | stock-only returns for the research default horizons, loaded in its own `Suspense` boundary from the observation endpoint (no benchmark, no filing); shows alignment status and the returns table; benchmark-relative and filing sections say "Not requested" with a link into the workspace |
| Listings | the listing history table (ticker, exchange, validity, primary, valid on event date) |
| Provenance | source name, provider type, ingested at, through the evidence drawer |

Descriptive evidence and the PIT verdict never share a frame (section 8.5.3).

### 8.4 Filings

#### 8.4.1 Filings index (`/filings`)

Eligibility is stated once, in the header note: *stored filings that have SEC FSDS financial-fact data, newest filing
date first; not every SEC filing; nothing about when a filing was available or tradable.* Every listed filing has FSDS
data, so there is no "FSDS available" column. Whether EPS is available is only known on the detail page.

| Column | Field | Tier | Treatment |
|---|---|---|---|
| Filing date | `filing_date` | P1, sticky | source metadata, plain date |
| Form | `form` | P1 | mono; amendments (suffix `/A`) carry an "Amendment" tag (formatting only, derived from the stored form) |
| CIK | `company_cik` | P1 | mono; `NOT_STATED` when null |
| Accession | `accession` | P1 | link to the filing view, carrying `cik` and the initial selectors |
| Report date | `report_date` | P2 | `NOT_STATED` when null |
| FSDS period end | `fsds_period_end` | P2 | `NOT_STATED` when the filing's submissions disagree (stated in the legend) |

Filters: `form` (exact select) and `cik` (ten digits), with the existing "exact: 10-Q does not include 10-Q/A" note.
No sorting controls (the API does not offer any). Joint accessions appear as two rows told apart by CIK (kept).
A legend line explains `NOT_STATED` and the offset-paging caveat.

#### 8.4.2 Filing view (`/filings/[accession]`)

Keeps the existing structure and rules, re-expressed in the state system:

1. A one-line boundary note ("Descriptive accounting data from SEC FSDS. No point-in-time availability claim. Not a trading signal.") as a `Callout`.
2. Selection form: period class and EPS kind (both required, nothing pre-selected from a missing value), carrying `cik` through.
3. **Current EPS** (status, reason, fact value), then **Comparative EPS, "as presented in this filing"**, then **EPS change** (current minus comparative, as computed by the API).

The phrase "as presented in this filing" is part of the comparative panel's **title line**, with a one-sentence
explanation beneath: *this is the prior-period figure shown in the same filing's own comparative column, not the
earlier filing's original number and not a restatement search.* Statuses, transitions and reasons are the backend's
and use the wording maps that exist today.

Percent semantics: `percentage_change` is a **ratio**; it is displayed as a percent (section 10.2) with the exact
ratio one interaction away. When it is undefined (`ZERO_COMPARATIVE`, `NEGATIVE_COMPARATIVE`) the panel says it is not
defined and why; it never shows `0%`.

Provenance of facts (fact id, tag, taxonomy, unit, period end, qtrs, segments, coreg, exact value) stays in an
"Evidence and exact values" disclosure in 11.1C; the research workspace's evidence drawer reuses the same content.

### 8.5 Research

#### 8.5.1 Research entry (`/research`)

A finder, not a result page. It contains: a one-paragraph explanation of what a study is (descriptive returns around
the event, the separate point-in-time verdict, the explicit refusals), the same ticker/date filters as Earnings in a
compact form, a compact list of events (event date, company, ticker, reported EPS, PIT status) each with an "Open
study" link, and a link to Saved research. It calls only `/earnings-events`. It replaces today's static principles
page; the four principles it states (point-in-time discipline, descriptive versus point-in-time, split-adjusted
prices only, explicit refusals) move into a short "How studies work" disclosure.

#### 8.5.2 Workspace layout (`/research/events/[eventId]`)

A **hybrid**: an event-scoped page whose inputs are URL state and whose result streams below.

```
+-------------------------------------------------------------------------------------------+
| Research / Example Co · 2026-04-30                                                         |
| Example Co · FY2026 Q1 · After close      [Stored event record]            [Copy link]     |  context bar
| Horizons 1,5,20 (default) · Benchmark: none · Basis SPLIT_ADJUSTED · Filing: none          |
+-----------------+-------------------------------------------------------------------------+
| CONFIGURATION   | RESULT STATUS  Descriptive: AVAILABLE | Point-in-time: REFUSED | Accounting: |
| sticky 17 rem   |                Not requested | 3 refusals                                     |
|                 +-------------------------------------------------------------------------+
| Benchmark [v]   | EVENT (stored observation)                                              |
| Horizons [____] | DESCRIPTIVE  what happened            [ AVAILABLE ]                     |
|  presets 1 5 20 |   session ladder                                                        |
| Filing [v]      |   return by horizon: chart + table (stock / benchmark / abnormal)       |
| Period class    |   EPS surprise                                                          |
| EPS kind        | POINT-IN-TIME  what could have been known      [ REFUSED ]                      |
| [Apply]         | ACCOUNTING & FILING                                                             |
| [Reset]         | REFUSALS (ledger)                                                               |
|                 | PROVENANCE                                                                      |
+-----------------+-------------------------------------------------------------------------+
```

- The page paints the header, context bar and configuration form immediately (they depend only on the URL, the event record and the benchmark list); the result region is a keyed `Suspense` boundary with a structured skeleton.
- Opening the page with no parameters is valid and runs the default study (default horizons, no benchmark, no filing) because the request is read-only and cheap. There is **no** separate "Run" step; **Apply** updates the URL.
- Below `1280px` the configuration collapses into a disclosure above the results; at `>= 1536px` the evidence inspector docks at the right instead of overlaying.

Configuration controls:

| Control | Behaviour |
|---|---|
| Benchmark selector | section 8.5.5 |
| Horizons | one text field (`1,5,20`), preset links for the default and common sets; validated locally: positive integers, no duplicates, at most 10, each at most 252; displayed ascending as the API echoes them |
| Filing | a select of the company's stored filings (`/filings?cik=<company cik>`, newest first, 50) labelled `form · filing date · accession`; a manual accession field (validated pattern) when the company has no CIK or the filing is not in the list; "Not linked" is an explicit option; nothing is pre-selected |
| Period class, EPS kind | required **together with** a filing, disabled and labelled when no filing is chosen; nothing pre-selected from a missing value |
| Apply / Reset | `Apply` submits the GET form; `Reset` links to the bare route |

A linked filing whose CIK differs from the event's company yields the backend's `CIK_MISMATCH_REFUSED`; the page
shows that refusal, it does not pre-empt it.

#### 8.5.3 Results hierarchy and descriptive versus point-in-time

Order, top to bottom (each is a section; every one remains readable when others refuse):

1. **Result status strip**: four compact segments (Descriptive state, Point-in-time state, Accounting state, refusal count), each an in-page anchor. This guarantees a PIT refusal is visible without scrolling and never reads as the whole result.
2. **Event** (stored observation): reported and estimated EPS, event date and timing, event instant or "Source gave a date only", `observed_at` and basis, event fingerprint via the evidence trigger.
3. **Descriptive study frame**: header `DESCRIPTIVE · what happened`, state badge `AVAILABLE | PARTIAL | UNAVAILABLE`. Contains the session ladder, the return-by-horizon chart and table, and the EPS surprise.
4. **Point-in-time frame**: header `POINT-IN-TIME · what could have been known`, state badge `REFUSED | EVIDENCE_ELIGIBLE`.
5. **Accounting and filing**: filing link (status, `PERIOD_UNVERIFIED` note, reason), disclosure evidence level (D0/D1/None with its rule that it implies no time), then the filing EPS panels when `EVALUATED`.
6. **Refusal ledger**: every `refusals[]` entry grouped by scope.
7. **Provenance**: collapsed summary; details through the evidence drawer.

**Visual separation of the two modes** (must be unmistakable and must survive greyscale):

| | Descriptive | Point-in-time |
|---|---|---|
| Header label | `DESCRIPTIVE` | `POINT-IN-TIME` |
| Rule | **solid** 3 px left rule, `--mode-descriptive` | **dashed** 3 px left rule, `--mode-pit` |
| Question line | "What happened after this event" | "What could have been known and acted on, given the availability evidence" |
| State badge | `AVAILABLE` / `PARTIAL` / `UNAVAILABLE` | `EVIDENCE_ELIGIBLE` / `REFUSED` |
| Background | `--surface` | `--surface` with a subtle diagonal-hatch edge on the header only (not behind data) |

A PIT refusal is rendered as a **callout inside the PIT frame only**: the backend's reason, the eligibility value,
and the fixed sentence *"This does not affect the descriptive study above."* The descriptive frame is never dimmed,
struck through, collapsed or reordered because PIT refused. When PIT is `EVIDENCE_ELIGIBLE` its study uses the same
`StudyView` component under the PIT frame, with the note that it is the same computation in point-in-time mode.

#### 8.5.4 Domain refusals: reusable presentation

One system, three scales (section 4): an inline `DomainState` (cell/value), a block `RefusalNotice` (section
replacement) and the `RefusalLedger` (all refusals). The ledger lists `scope` (EVENT, MARKET, BENCHMARK, FILING_LINK,
ACCOUNTING, POINT_IN_TIME) as group headings, each entry showing the backend `code` (mono), the state label, the
`reason` and, for per-horizon entries, the horizon. Entries link to the section they explain. The count in the status
strip equals the ledger length.

Examples and their state: `NO_ALIGNMENT` -> `UNAVAILABLE`; `MISSING_ANCHOR_PRICE` -> `MISSING`;
`MISSING_TARGET_PRICE` -> `MISSING` (the calendar proves the N-th session, no price is stored for it);
`INSUFFICIENT_FORWARD_SESSIONS` -> `MISSING` (the calendar cannot prove the N-th session at all);
`AMBIGUOUS_PRICE_SOURCE` -> `AMBIGUOUS`; `COMPARATIVE_MISSING` -> `MISSING`; `CIK_MISMATCH_REFUSED` -> `REFUSED`;
`FILING_NOT_STORED` -> `MISSING`. Full table in Appendix B.

#### 8.5.5 Explicit benchmark selection

| State | Appearance |
|---|---|
| **Unselected** (the initial state) | the control reads **"None (stock returns only)"**; helper text: "Choose a benchmark to compute benchmark-relative and abnormal returns. Nothing is chosen for you." |
| Selector | a native `<select>` (accessible, keyboard-complete, no JS) fed by `GET /benchmarks?limit=200`. Active benchmarks first in the API's `(name, id)` order, then an "Inactive" `optgroup` in the same order. Option text: `name (symbol) · currency`; inactive options add "· inactive". When names collide, a short id suffix (first 8 characters) disambiguates. |
| Inactive | selectable (a saved study may reference it), labelled inactive, with a callout once selected: "This benchmark is marked inactive: no longer used for new research, retained for history." |
| Selected | a chip in the context bar: `Benchmark: name (symbol) x`, where the `x` is a link that removes the parameter |
| Unknown id in the URL | the selector shows "Benchmark id not in the stored list" with the raw id; the request is still made and the backend's answer is shown |
| More than 200 benchmarks | the selector lists the first 200 and a note shows the total (an accepted limit, risk R6) |
| Before selection, abnormal-return rows | `NOT_REQUESTED` ("No benchmark selected") with a link to the configuration, **not** `UNAVAILABLE`, even though the API reports `BENCHMARK_RETURN_UNAVAILABLE` for those horizons: the UI reads the benchmark status of the same horizon (`NO_BENCHMARK`) to choose the state |

If exactly one benchmark exists, it is still not selected for the reader. The selection is never stored.

#### 8.5.6 Event-study visualisation

Two charts, both **server-rendered SVG** with no chart library and no client JavaScript. Each has a data table
beside it that is the source of truth; the chart is a supplement.

**Chart 1: Return by horizon (the primary chart)**

| Property | Specification |
|---|---|
| Question answered | "At each horizon, how did the stock move, how did the chosen benchmark move over the same sessions, and how far apart were they?" |
| Mark | a **dumbbell** per horizon: a filled circle (stock return), a hollow diamond (benchmark return), a connecting segment whose length is the abnormal return (stock minus benchmark) |
| X axis | categorical, equally spaced, labelled `+1`, `+5`, `+20` with the axis title "sessions after the anchor session". Categorical on purpose: horizons are discrete chosen measurements, not a continuous time axis |
| Y axis | return as a percent, symmetric around a labelled **zero line**, "nice" ticks, axis title "Return (%)"; the underlying values are ratios and are converted by exact shifting for labels |
| Series | Stock (`--chart-stock`), Benchmark (`--chart-bench`), Abnormal (`--chart-abnormal`, the connector). Shape and fill differ so the chart reads in greyscale |
| No connecting line across horizons | the horizons are separate measurements from the same anchor, not a sampled price path; joining them would imply interpolated sessions that were never observed |
| Tooltip / inspection | each horizon is a focusable group (`tabindex="0"`) whose accessible name and native `<title>` give: anchor session and close, target session and close, exact return strings, benchmark return, abnormal return |
| Missing data | a horizon whose status is not `AVAILABLE` renders an empty slot with the state glyph and label at the baseline (for example "Missing target price"); **no marker, no interpolation, no zero**. If only the benchmark is missing the stock marker still shows and the benchmark slot carries `NOT_REQUESTED`/`MISSING` |
| Refusal behaviour | if alignment is refused the chart is replaced by the `RefusalNotice`; the table beneath shows the per-horizon statuses |
| Colour and scale | markers use the **direction** hues only through position relative to the zero line; the series colours are separate tokens (section 10.5) |
| Geometry rule | the geometry module may convert an exact string to a JS number **only to compute coordinates**; it never produces a displayed label (labels come from exact-string formatting) and never sums or averages returns |

**Chart 2: Session ladder (alignment)**

| Property | Specification |
|---|---|
| Question answered | "Which exchange sessions did the event map to, and which sessions were the horizons measured to?" (sessions come from the exchange calendar since Step 11.2E; prices are what is stored) |
| Layout | a horizontal categorical strip of **session dates**: `event session` (if the event date is itself an exchange session), the **anchor** `first_post_event_session` (S0), then the target sessions `S+N` for each requested horizon |
| Event marker | placed **between session slots** according to the declared timing: `AFTER_MARKET` -> after the event session's slot; `BEFORE_MARKET` -> before the anchor's slot. It is labelled with the timing text ("After close") and the event date. **It is never placed at a clock time** |
| Not to scale | slots are equal width and the legend says "stored trading sessions only; weekends and holidays are not shown" (the legend wording predates 11.2E and is carried: see the 12E register) |
| Refusals | `UNKNOWN_TIMING` and `INTRADAY_PRECISION_UNSUPPORTED` replace the ladder with a `REFUSED` notice quoting the backend reason; `NO_SESSION_DATA` -> `MISSING` (the backend no longer emits it for an event the calendar can align; the status stays in the contract). A target session that is proven but has no stored price keeps its date and carries the note "Price not stored for target session"; a horizon the calendar cannot prove has no date at all |

**Temporal precision rule.** No visual element positions an event, an observation or a filing at an intraday point.
`event_timestamp` and `observed_at` are shown only as text (UTC instants) in evidence panels, never on a time axis.
The only axis of time in any chart is a sequence of exchange session dates.

#### 8.5.7 Tables beneath the charts

The return table has one row per horizon: Horizon, Anchor session, Anchor close, Target session, Target close,
Stock return, Benchmark return, Abnormal return, Status. Prices are exact strings; the three returns are percent
with the exact ratio on interaction; a non-`AVAILABLE` cell uses the state system. The abnormal-return column header
reads "Abnormal return (stock minus benchmark, percentage points)" and its cell tooltip repeats the backend
`price_basis_note` once: *split-adjusted price returns, dividends excluded; a simple market-adjusted figure with an
implicit beta of 1, not a CAPM or factor alpha.*

### 8.6 Saved research

**Reality check, stated plainly in the UI.** There is no public save: baselines are created by an internal service,
so this surface lists and verifies what exists. The empty state says so (section 11.2). This is open decision D1.

#### 8.6.1 Saved research list (`/research/saved`)

Page size **20** (decision D8), so the per-row event enrichment stays small.

| Column | Field | Treatment |
|---|---|---|
| Saved (UTC) | `created_at` | labelled "when this baseline was saved (persistence metadata, not data availability)" |
| Event | `request.event_id` | resolved to `company · event date` by a parallel `GET /earnings-events/{id}` (deduplicated, at most 20); `404` -> "Event no longer stored" (not an error); failure -> short id with "not resolved" |
| Study | `request.*` | `Horizons 1,5,20` · benchmark name from the benchmark list (or "none") · filing accession + `EPS kind` (or "none") |
| Reproducibility | `reproducibility_level` | badge `COMPLETE` / `PARTIAL` |
| Gaps | `reproducibility_gaps` | count with the gap names in an expandable cell |
| Manifest | `manifest_fingerprint` | first 10 hex characters + ellipsis, copy button, full value on focus |
| Result | `result_fingerprint` | same |
| Open | link | to the record |

Ordering is the API's (newest saved first). The list does not re-verify and says so in the header note ("summaries
of stored columns; integrity is validated when a record is opened"). A `500` from the list is the generic integrity
message, not an empty list.

#### 8.6.2 Saved research detail (`/research/saved/[recordId]`)

The page is built around one distinction, stated in the layout: the **historical stored baseline** versus the
**current rerun**.

```
SAVED BASELINE  (immutable)                      CURRENT RERUN  (not saved)
 identity + request                               [ Verify against current stored evidence ]   <- link ?verify=1
 reproducibility: COMPLETE / PARTIAL + gaps        result: MATCH | EVIDENCE_INCOMPLETE |
 fingerprints (manifest, result)                           MANIFEST_CHANGED | RESULT_CHANGED
 evidence summary (from the stored manifest)       changed sections, changed paths
 stored result inspector (collapsed)
```

| Block | Content |
|---|---|
| Header | record id (short, copyable), saved time, level badge, actions: "Re-run this request live" (workspace link), "Stored event record" |
| Request | event (resolved), horizons, benchmark (name), filing + selectors, price basis |
| Reproducibility | the level badge, the gap list with human sentences (Appendix C), and the always-visible residual-assumptions sentence |
| Fingerprints | manifest and result digests (full, mono, copy); request/methodology/evidence digests appear in the verification panel |
| Evidence summary | derived defensively from documented manifest paths only (event pinned with source/run, stock bars pinned, benchmark bars pinned, filing/accounting state); anything not recognised is simply not summarised |
| Stored result | a collapsed **result inspector** (a key/value tree with exact strings, grouped by top-level key). It is *not* rendered through the research `StudyView`, because the stored result is the domain's internal structure, not the public observation shape (gap N8). The "Re-run live" link provides the typed view of current data |
| Verification | section 9.3 |

Integrity: the detail is only ever shown after the backend's load service re-hashed the stored text (a corrupt
record is the generic `500` and shows the fixed message, no content).

---

## 9. Provenance, reproducibility and verification

### 9.1 Provenance interaction: `EvidenceTrigger` and `EvidenceDrawer`

Every important research number has a path to its evidence, but provenance is **never inline by default**.

- An `EvidenceTrigger` is a small text button ("Evidence") placed beside a value group, with an accessible name that
  says what it opens ("Evidence for the +5 session stock return").
- It opens an `EvidenceDrawer`: a native `<dialog>` used as a right-hand panel (28 rem; a full-height sheet below
  `768px`; docked in the inspector column from `1536px`). It traps focus, closes on Esc and on the close button, and
  **returns focus to its trigger**. Its content is server-rendered and passed as children, so the drawer performs no
  fetch.
- A collapsed **Provenance** section at the end of the workspace lists the same items for readers who want
  everything at once.

Content by datum:

| Datum | Evidence items shown |
|---|---|
| Stored event / EPS | source (name when known from the event record, otherwise id), provider type, event fingerprint, ingestion run id or "No ingestion run recorded", `observed_at` and basis, ingested at (event record only) |
| Stock return (a horizon) | resolved listing and security ids, price source id, price basis `SPLIT_ADJUSTED` and the backend's basis note, anchor session and close, target session and close |
| Benchmark return | benchmark name, symbol, currency (from the selector list) and id, anchor and target sessions and closes |
| Filing link and disclosure | accession, form, CIK, filing source id, filing ingestion run id, disclosure level and stored 8-K items (no time) |
| Accounting facts | fact ids, tag, taxonomy, unit, period end, qtrs, segments, coreg, exact value, **artifact SHA-256 per role** (current / comparative) |
| Saved baseline | manifest and result fingerprints, level, gaps, methodology label |

Identifiers are shown as short ids (first 8 characters) with a copy control and the full id on focus. The API gives
names only for the event source and (via the benchmark list) the benchmark; other sources appear as ids and the
drawer says "name not available from the API" (gap N4, tolerable).

### 9.2 Reproducibility treatment

`ReproducibilityBadge` has exactly two values, both shown with text and a glyph:

| Level | Glyph | Label | One-line meaning (always available as the badge's description) |
|---|---|---|---|
| `COMPLETE` | filled ring | "Complete" | "Every consumed piece of evidence is pinned to a recorded ingestion run. Residual assumptions below still apply." |
| `PARTIAL` | half-filled ring | "Partial" | "Some consumed evidence has no complete immutable provenance pin. The numbers are not wrong; the evidence trail is incomplete." |

**`PARTIAL` is never styled as an error, never red, never "failed" or "incorrect".** It uses the caution tint and the
neutral wording above. Each gap is listed with a sentence (Appendix C), for example `BENCHMARK_BARS_WITHOUT_INGESTION_RUN`:
"At least one benchmark level used here has no ingestion run (every level stored before migration 0013 is unstamped)."
The residual assumptions are shown on every saved record: *stored price bars and filings are treated as immutable by
convention; listing reference rows are not snapshotted, only the resolved identity is pinned.* An unsaved live study
shows **no** level: "Reproducibility is assessed when a study is saved as a baseline."

### 9.3 Verification UX

The action is a plain link (`?verify=1`). The page renders the verification region in a keyed `Suspense` boundary, so
the rerun is server-driven, needs no client fetch, and a refresh reruns it against the then-current stored evidence.
The panel is titled **"Current rerun (not saved)"** and states what it compares: "this stored baseline against the
evidence stored now". No time of verification is shown (the API provides none).

| Status | Presentation | Always-visible copy |
|---|---|---|
| `MATCH` | the only place a success tone is used (system verification, not a financial value); label "Reproduced" with the code `MATCH` | "Current stored evidence reproduces this baseline: the request, methodology, evidence and result fingerprints agree. This does not prove that unpinned external source data still exists." |
| `EVIDENCE_INCOMPLETE` | caution tone, label "Matches, incomplete evidence" | "The rerun matches, but the baseline is PARTIAL, so this is not a complete reproducibility claim." (not an error) |
| `MANIFEST_CHANGED` | caution tone, label "Evidence or request changed" | "The request, methodology or consumed evidence now differs from the baseline." plus the changed sections (`request`, `methodology`, `evidence`) as chips |
| `RESULT_CHANGED` | caution-strong tone, label "Same inputs, different result" | "The same manifest now produces a different result: a methodology-version error, unpinned evidence, data mutation or a bug." |

Below the status: a fingerprint comparison (request, methodology, evidence, manifest, result; expected versus actual,
differing rows marked with a **"differs"** text tag, not colour alone) and the level of the rerun.

**Changed paths without dumping JSON.** The API returns dotted paths, not values. The UI renders a **path list**:

1. paths are grouped by their first segments (`evidence.stock`, `evidence.benchmark`, `request.horizons`, `result.descriptive...`) under readable headings;
2. each path is humanised ("Evidence > Stock > Bar 2 > close") with the raw dotted path available on focus and copy;
3. for each path the **baseline value** is shown, looked up in the stored manifest/result tree the page already loaded; the current value is shown as "not returned by the verify endpoint" with a link to the live study (gap N5, tolerable);
4. at most 100 paths are listed with "and N more" and an expand control.

Errors: `404` (record gone), `409` ("The earnings event of this record is no longer stored. The baseline is intact but
cannot be rerun."), `500` (the fixed integrity message, no content), `503` (generic unavailable).

### 9.4 Reveal motion

The verification result fades and settles in over 120 ms (opacity and a 4 px translate). With reduced motion it simply
appears. Nothing else in the verification flow is animated.

---

## 10. Visual system

### 10.1 Visual identity

**Character**: serious, analytical, precise, modern, financial, dense, calm. It should read like an annotated
financial statement or a research ledger, not a trading terminal.

Defining traits:

- **Rules instead of boxes.** Hairline 1 px dividers and section labels do most of the grouping; boxes (frames) are reserved for things that need a title and a state.
- **Small radii** (4 px for frames and tables, 6 px for controls); **no shadows** except on the overlay drawer.
- **Numbers are the heroes**: tabular figures, restrained size, no decorative scale.
- **One restrained accent** (steel blue) for links, focus and selection; one distinct **point-in-time hue** (violet) used only to mark the PIT mode.
- The wordmark `QF/` in mono with an accent slash (already present) is the only brand flourish.
- Small uppercase tracked section labels (already present) give the page its rhythm.

Explicitly avoided: generic shadcn dashboard look, large rounded cards, gradients, glow, glassmorphism, fake terminal
green, neon, AI sparkles, decorative gauges, sparklines without a question, and any red/green profit-and-loss
colouring.

### 10.2 Financial number system

The rule that governs everything: **the API value is storage; the display is a formatting step; they are separate and
the storage value is never changed.** Parsing keeps the exact string. Display is produced by pure functions over the
string (the existing `formatDecimal`, `formatRatioAsPercent`, BigInt based). A JS `number` is never produced from a
financial string except inside the chart geometry module, which never emits a label.

| Kind | Source field examples | Display | Exact value |
|---|---|---|---|
| Price / level | `anchor_close`, `target_close` | the exact string as sent (`182.3400`), right-aligned, tabular, no currency symbol (currency is not stated per price) | already exact |
| EPS (per share) | `reported_eps`, `estimated_eps`, fact `value` | exact string, scale preserved | already exact |
| EPS surprise / EPS change (absolute) | `absolute_surprise`, `eps_change` | **signed** exact string (`+0.2000`, `-0.0100`); zero has no sign | already exact |
| Ratio shown as percent | `return_value`, `benchmark_return`, `abnormal_return`, `surprise_ratio`, `percentage_change` | percent to 2 decimals with exact integer arithmetic and round-half-away-from-zero (`+7.50%`), sign for non-zero, `0.00%` for exact zero | the exact ratio on hover, focus and in the evidence drawer |
| Money (revenue) | `reported_revenue`, `estimated_revenue` | exact digits with grouping of the integer part only (`94,836,000,000.0000`): lossless, no rounding, no abbreviation | on the cell's accessible description |
| Count / integer | `qtrs`, horizon | plain integer | n/a |
| Null from source | any nullable | `NOT_STATED` | n/a |
| Refused / missing / etc. | statuses | the state system | n/a |

Special cases:

- **Zero**: shown as the exact string (`0`, `0.0000`); a ratio that is exactly zero is `0.00%`. Neither is styled differently from other numbers.
- **Non-zero rounding to zero**: a ratio that rounds to `0.00%` is shown with the existing "Not zero: the ratio is too small to show at this precision" note and its exact value (the existing rule is kept).
- **Negative**: the ASCII hyphen-minus of the API string is kept in text (so a copied value is exact); the sign is also the leading character of a right-aligned tabular cell.
- **Never**: `0` for a missing value, a blank cell, a rounded price, a float intermediate, scientific notation, or a percent computed on the client from two other values.

Pure display helpers are a single module (`lib/format`) extended in 11.1B; the new `FinancialValue` component wraps
them and is the only component allowed to render a financial string.

### 10.3 Percentage semantics (the ratio boundary)

The backend's ratios are **ratios, not percentages**: `0.125` means one eighth. The boundary is documented once and enforced by types:

- Wire and storage: `Ratio` (a branded exact string, `0.125`).
- Display: `PercentValue` takes a `Ratio` and shifts the decimal point by two places exactly (`12.50%`), rounding only the displayed digits.
- The component, not the call site, appends `%`; a call site cannot pass a ratio to `FinancialValue kind="price"`.
- **Abnormal return** is the difference of two returns, so its unit is percentage points; its column header says so and the cell shows `%`.
- EPS change `percentage_change` is defined only for a positive comparative base (the API refuses zero and negative bases); the UI never computes an alternative.

### 10.4 Positive, negative and neutral

Restrained and redundant, never evaluative.

| Aspect | Rule |
|---|---|
| Default | numbers are **neutral** (`--text`). A sign character is the primary cue: `+` on non-zero positive signed values, `-` on negatives |
| Never | red/green profit-and-loss colouring; the words gain, loss, win, bullish, bearish; icons implying good or bad |
| Optional **direction tint** (preference, default **off**) | when on, return and change cells take the direction hues (`--dir-positive` teal, `--dir-negative` orange) **and** a leading arrow glyph (`▲` / `▼`) so colour is never alone. Tint means "above or below zero", not "good or bad" |
| Charts | direction is encoded by **position relative to the zero line**; the series colours are independent of sign |
| Statuses | the status palette (info, caution, danger) is separate from the direction palette; `danger` is only for application errors |
| Accessibility | all text/glyph tokens meet 4.5:1 in both themes (measured, section 10.5); direction marks meet 3:1 |

(The existing code and tests already enforce "no evaluative colour" for EPS change. V2 keeps that default and makes the
optional tint an explicit preference.)

### 10.5 Design tokens

Semantic tokens only; pages never use a literal colour. Tailwind 4 `@theme` entries map to these CSS variables. The
current token names are kept as **aliases** during migration (`surface-0..3`, `border-subtle`, `border-strong`,
`text-primary/secondary/muted`, `accent`, `status-ok/warn/down`) so 11.1B can land without touching every component.

Dark is the default; light is a full peer. Every pair below was measured (WCAG relative luminance); `AA text` is 4.5:1,
`AA UI` is 3:1.

| Token | Role | Dark | Light |
|---|---|---|---|
| `--bg` | page background | `#0a0e13` | `#f3f5f8` |
| `--surface` | primary surface, frames, tables | `#0f141b` | `#ffffff` |
| `--surface-elevated` | hover rows, inputs, subtle fills | `#151b24` | `#f8f9fb` |
| `--surface-raised` | drawers, popovers, header strip | `#1c232e` | `#eef1f5` |
| `--border-subtle` | hairlines, dividers | `#1f2732` | `#e2e7ed` |
| `--border` | frame and table borders | `#2d3743` | `#cdd5de` |
| `--border-control` | **form control boundaries** (new; fixes F2) | `#6b7f92` (4.18:1 on elevated, 4.68:1 on bg) | `#7b8794` (3.66:1 on surface, 3.35:1 on bg) |
| `--text` | primary text | `#e7edf4` (15.7:1 on surface) | `#111820` (17.9:1) |
| `--text-2` | secondary text | `#9fb0c0` (8.3:1) | `#3b4856` (9.3:1) |
| `--text-3` | muted text, labels | `#7d92a4` (5.7:1 on surface, 4.9:1 on raised) | `#566474` (6.1:1 on surface, 5.3:1 on raised) |
| `--accent` | links, focus, selection | `#58a6ff` (7.3:1) | `#0b63c4` (5.8:1) |
| `--accent-subtle` | selected fill | `#16283d` | `#e1eefc` |
| `--accent-contrast` | text on a solid accent | `#0a0e13` | `#ffffff` |
| `--info` | informational state, ambiguity | `#5cc8e0` (9.5:1) | `#0a6f86` (5.8:1) |
| `--warning` | caution states (missing, partial) | `#d4a03c` (7.8:1) | `#8a5a00` (5.9:1) |
| `--danger` | application errors only | `#f0645a` (5.9:1) | `#b42318` (6.6:1) |
| `--success` | **system health and verification MATCH only** | `#47b357` (6.9:1) | `#17722f` (6.0:1 on surface, 5.3:1 on raised) |
| `--mode-descriptive` | descriptive frame rule and label | = `--accent` | = `--accent` |
| `--mode-pit` | point-in-time frame rule and label | `#b4a0ff` (8.3:1) | `#5b3fc4` (7.1:1) |
| `--dir-positive` | above-zero direction mark | `#4fc3b0` (8.6:1) | `#0f766e` (5.5:1) |
| `--dir-negative` | below-zero direction mark | `#e8935e` (7.7:1) | `#b45309` (5.0:1) |
| `--chart-stock` | stock series | `#58a6ff` (7.3:1) | `#0b63c4` (5.8:1) |
| `--chart-bench` | benchmark series | `#c9d4df` (12.3:1) | `#4a5666` (7.5:1) |
| `--chart-abnormal` | abnormal-return connector | `#e8935e` (7.7:1) | `#b45309` (5.0:1) |
| `--chart-grid` | gridlines | `--border-subtle` | `--border-subtle` |
| `--chart-zero` | zero line | `#6b7f92` (4.5:1) | `#7b8794` |
| `--hover` | row/control hover | `--surface-elevated` | `--surface-raised` |
| `--selected` | selected row/option | `--accent-subtle` + 2 px accent left bar | same |
| `--focus` | focus ring | `--accent`, 2 px, 2 px offset | same (5.8:1 on surface) |

Notes: `success` is not used for any financial value, nor for point-in-time eligibility (that uses the PIT hue);
`danger` is not used for any domain state. The unit test in 11.1B recomputes every pair above from the actual CSS
and fails below the stated thresholds (jsdom cannot measure contrast, so the token file is the tested artefact).

Theme mechanics: `:root` carries dark; `@media (prefers-color-scheme: light)` carries light unless
`html[data-theme="dark"]`; `html[data-theme="light"]` forces light. The cookie `qf-theme` (`system` default) is read in
`RootLayout` on the server and sets `data-theme` and `color-scheme`, so there is **no flash** (see the Next guide on
preventing flash before hydration). The theme control is a three-way segmented control in the header strip. SVG
charts colour themselves with the CSS variables, so a theme change needs no re-render.

### 10.6 Typography

| Role | Family | Size / weight / line | Notes |
|---|---|---|---|
| Brand / navigation | UI sans | 13 px / 500 / 1.2 | wordmark in mono |
| Page title (h1) | UI sans | 20 px / 600 / 1.25, tracking -0.01em | one per page |
| Section label (h2) | UI sans | 11 px / 500 / uppercase, tracking 0.14em, `--text-3` | existing style, kept |
| Subsection (h3) | UI sans | 13 px / 600 | |
| Body | UI sans | 14 px / 400 / 1.5 | reading width capped at 64 rem |
| Label | UI sans | 12 px / 500, `--text-3` | form labels, key-value keys |
| Table text | UI sans | 12 px / 400 / 1.35 | tabular figures on numeric cells only |
| **Financial number** | UI sans, **`font-variant-numeric: tabular-nums`** | inherits (12 to 14 px) | not monospace; right-aligned in columns |
| Code / identifier | mono | 12 px | **only** fingerprints, digests, UUIDs, accession, CIK, tags, JSON paths, status codes |
| Metadata / helper | UI sans | 11 to 12 px, `--text-3` | |

Family decision (D2): a **self-hosted variable Inter** subset via `next/font/local` (the existing CSS already tunes
for Inter features), with the system sans stack as the fallback and `size-adjust` to avoid layout shift; the mono is
the system mono stack. The woff2 file is committed as an asset (the Google-fonts loader needs a build-time network
fetch, which a research workstation build should not require). If the human prefers zero font assets, the system stack
is acceptable and everything else in this spec still holds. The global `"tnum"` on `body` (F3) is removed; tabular
numerals are applied by the `.num` utility on numeric cells only.

### 10.7 Spacing, density and the table system

Base scale 4 px: `1=4, 2=8, 3=12, 4=16, 6=24, 8=32`.

| Aspect | Compact (default) | Comfortable (preference) |
|---|---|---|
| Table row height | 28 px (12 px text, 4 px vertical padding) | 36 px |
| Table header | 32 px | 36 px |
| Form control height | 32 px (40 to 44 px below 768 px) | 36 px |
| Frame padding | 12 px | 16 px |
| Section gap | 16 px | 24 px |
| Page padding | 16 px, 24 px from `sm` | same |

Maximum widths: reading `64rem`, workspace `96rem`, table fluid to `120rem`.

#### 10.7.1 One table system (`DataTable`)

For earnings, filings and saved research.

- **Header**: sticky inside the scroll container, 12 px/500, `--text-3`, bottom border `--border`. Columns declare `align` (`start` | `end`), `priority` (`P1` | `P2` | `P3`) and `sticky` (first column only).
- **Alignment**: text and dates left; **numeric columns right with tabular figures**; status columns left.
- **Rows**: 1 px `--border-subtle` bottom border; hover `--hover`; a "selected" row (when one is needed) uses `--selected` with a 2 px accent bar; no zebra striping.
- **Interaction**: the primary link is in the first cell; whole-row click targets are **not** used (they hide the real link from keyboard and screen readers). Focus ring on links and on the scroll region.
- **Semantics**: a real `<table>` with `<caption>` (visually shown as the table title), `scope="col"` headers, `aria-sort` only when a column is genuinely sorted by the server (none are in V2).
- **Pagination** (`Pagination`): a `nav aria-label="Pagination"` with "Rows 51 to 100 of 1,234", Previous and Next links, and "Page 2 of 25" text; page links keep every other filter in the URL; the offset is bounded to the API's limit; the offset-paging caveat appears once in the legend.
- **Empty / filtered-empty / past-the-end / error** are in-place states from section 11.2.
- **Responsive**: section 10.7.2.

#### 10.7.2 Table responsiveness

Dense financial tables are **not** turned into stacked cards.

| Width | Behaviour |
|---|---|
| `>= 1280` | all columns |
| `768 to 1279` | P3 columns hidden; a "Columns" note says "N more columns at wider widths" |
| `< 768` | P1 + the identity column only visible by default; the rest are reachable by horizontal scroll |
| Always | the table lives in a bordered scroll container (`role="region"`, `tabindex="0"`, labelled "<title>, scrollable"); the first (identity) column is `position: sticky; left: 0` with an opaque background and a hairline edge; edge shadows are a CSS cue only |

### 10.8 Card system

Do not wrap every section in a card. Four patterns:

| Pattern | Use | Appearance |
|---|---|---|
| **Section** (default) | grouping a topic | section label + 1 px rule above; no box |
| **Frame** | a block that needs a title **and** a state: the descriptive frame, the PIT frame, the accounting frame, Overview panels | 1 px `--border`, 4 px radius, header strip with label and state badge, 12 px padding; never nested |
| **Stat cluster** | up to six related label/value pairs | inline, hairline-separated, no box |
| **Callout** | a boundary note, a refusal, a legend | left rule (solid, dashed or dotted per the state), no fill, 8 to 12 px padding |

No metric "tiles" with large numbers; a number is shown at body or table size beside its label.

### 10.9 Dark and light

Both modes derive from the same semantic tokens with independently measured values (section 10.5); light is not an
inversion. Status semantics keep their text labels and glyphs in both, so legibility never depends on a hue.
Charts use the chart tokens and remain legible: series differ in shape and fill as well as colour.

### 10.10 Motion

Sparing, functional, always reduced-motion safe (the existing global rule collapses durations).

| Where | Motion |
|---|---|
| Evidence drawer | 160 ms ease-out slide (transform) and a 120 ms backdrop fade; instant with reduced motion |
| Navigation drawer (mobile) | same as above |
| Result region after Apply | the skeleton cross-fades to content in 120 ms (opacity only) |
| Verification result | 120 ms fade + 4 px settle |
| Skeleton | a slow 1.4 s opacity pulse; **none** with reduced motion (static fill) |
| Everything else | none: no page transitions, no hover animation, no count-up numbers, no decorative loops |

---

## 11. Loading, empty and error states; accessibility; responsive behaviour

### 11.1 Loading states

Progressive structure stays visible; a full-page spinner is never used.

| Page | Immediately painted (no data wait) | Streamed in `Suspense` | Skeleton |
|---|---|---|---|
| Shell | rail, header strip | API status | a neutral status chip placeholder |
| Overview | page header, "Start research", "Coverage and limits" | System, Earnings, Filings, Saved research (each its own boundary) | per-panel row skeletons (3 to 8 rows) |
| Earnings, Filings, Saved list | page header, filter form (from the URL), table caption | the table + pagination | header row + 10 rows with realistic column widths |
| Event record | breadcrumb, header from the event record | market reaction preview | a 3-row returns table |
| Research workspace | breadcrumb, context bar, configuration | the whole result region (keyed by the canonical query) | status strip, three frame skeletons, chart placeholder at its final size (no layout jump) |
| Saved detail | header, baseline | the verification region (keyed by `verify`) | status banner + five fingerprint rows |

Rules: skeleton blocks have **the dimensions of the final content** (no layout shift); the skeleton region carries
`aria-busy="true"` and a visually hidden "Loading ..." status; the pulse is disabled under reduced motion; a segment
`loading.tsx` exists for each route as the fallback for direct loads.

### 11.2 Empty states

Different situations, different words and no fake data. Each is `role="status"`.

| Situation | Where | Message |
|---|---|---|
| **No data exists** | Earnings | "No earnings events are stored. Events appear after ingestion has loaded them." |
| | Filings | "No FSDS-backed filings are stored. This does not mean no filings exist." |
| | Saved research | "No baselines have been saved. Baselines are created by the internal save service; the UI has no save action yet." |
| **Filters returned nothing** | lists | "Nothing matches these filters." + **Clear** |
| **Past the last page** | lists | "This page is past the last result." + link to page 1 |
| **Optional section not requested** | research | the `NOT_REQUESTED` state with the action that would request it ("Select a benchmark", "Link a filing") |
| **Research configuration incomplete** | workspace | an inline local error before any request: "A filing needs both a period class and an EPS kind." The study for the *valid* part is not blocked |
| **No related items** | event record | "No listings are stored for this company." |

### 11.3 HTTP and transport errors (never conflated with domain refusals)

All are `ERROR` (`role="alert"`, danger outline). Messages never contain a URL, SQL, driver text, exception text or a
stack. The raw `detail` is shown only when the backend defines it as a contract (the fixed strings below).

| Condition | Title | Body | Actions |
|---|---|---|---|
| **404** | "Not found" (route-specific: "No stored event has this identifier") | what was looked for, no guess | link to the parent list |
| **409** (verify) | "Cannot re-run this record" | "The earnings event of this record is no longer stored. The baseline is intact but cannot be rerun." | link to the record |
| **422** | "The API rejected these parameters" | the backend's `loc` and `msg` for each field, as text | "Clear" / edit the form. The UI validates locally first, so this should be rare |
| **500** | "The server hit an unexpected error" | "Details are in the server log." plus the Next digest when present | Retry (same URL) |
| **500, record integrity** | "This saved record failed integrity validation" | "It is not displayed. This is a server-side data problem, not a verification result." | back to the list |
| **503** | "Temporarily unavailable" | "The database cannot be reached right now. This is a service problem, not an empty result." | Retry |
| **Network / timeout** (status 0) | "Cannot reach the QuantForge API" | "The request timed out or the service is not running." (no URL) | Retry |
| **Contract drift** | "The API returned data this version of the app does not understand" | one line in production; the field path and value shape in development only | none |

Domain refusals use the state system (section 4), never this table.

### 11.4 Accessibility

Target: WCAG 2.2 AA, with specific commitments.

| Area | Commitment |
|---|---|
| Keyboard | everything reachable and operable by keyboard; logical tab order = visual order; scroll regions focusable; no keyboard traps except the modal drawer, which traps focus, closes on Esc and returns focus to the trigger |
| Focus | a visible 2 px accent ring on every focusable element (`:focus-visible`), 3:1 or better against its background in both themes; not removed by components |
| Headings | one `h1` per page; `h2` for sections, `h3` for subsections, no skipped levels; frames use `aria-labelledby` |
| Landmarks | skip link, `nav` (primary, breadcrumb, pagination), `main`, `aside role="note"` for boundaries |
| Tables | real `<table>`, `<caption>`, `scope`, `aria-sort` only when true; non-`VALUE` cells expose their state and reason in the accessible name; scroll regions labelled |
| Charts | each chart is an `<svg>` with a `<title>` and `<desc>` summarising it, focusable groups with full accessible names, and the **equivalent data table is always on the page** |
| Forms | every control has a `<label>`; errors are associated with `aria-describedby` and announced; required selections are marked in text, not just colour; help text is a real element |
| Announcements | a polite live region announces result counts and "Loading results" / "Results loaded"; errors use `role="alert"`; refusals use `role="status"` |
| Colour | all text and glyph tokens >= 4.5:1, UI boundaries and focus >= 3:1, in both themes (measured); **no information is carried by colour alone**: every state has a label, a glyph and a border style |
| Non-colour status | the ten states differ by label, glyph and outline style; sign by character; direction tint always has an arrow |
| Motion | reduced motion honoured globally; no autoplay, no flashing |
| Target size | 24 px minimum (WCAG 2.2), 44 px controls on mobile |
| Language and titles | `lang="en"`, unique page titles from the template |

### 11.5 Responsive strategy (summary)

See sections 6.2 and 10.7.2. In short: rail to drawer below `1024`; research configuration to a disclosure below
`1280`; the inspector docks only from `1536`; tables scroll horizontally with a sticky identity column and hide P3 then
P2 columns; the evidence drawer becomes a full-height sheet on mobile; controls grow to 44 px below `768`.

---

## 12. Engineering design

### 12.1 API client strategy: a hybrid (decision D6)

**Current reality.** Hand-written strict parsers for health, earnings and filings (about 700 lines), two incompatible
result types (F4), exact-decimal discipline, a backend-pinned contract fixture (`contract/vertical.json`). The OpenAPI has
**81 schemas**; the research observation alone is about 35 nested types. Hand-writing every decoder and type without
a drift check would be the largest source of risk in V2.

**Options considered.**

| Option | For | Against |
|---|---|---|
| A. Generated client from OpenAPI | no hand typing; fast | the generated runtime does not enforce "decimals are strings", exact enums or non-absent nulls; opaque; replaces the project's strict style |
| B. Hand-written strict clients | maximal control; matches today | 35+ nested types by hand; silent drift between types and the backend; high maintenance |
| **C. Hybrid (chosen)** | compile-time drift detection from generated types **and** runtime strictness from hand-written decoders | one new dev dependency; a snapshot to maintain |

**Chosen design.**

1. A committed snapshot `apps/web/contract/openapi.json` exported from the backend app. A backend **test** (not source) asserts the committed snapshot equals `app.openapi()`, so a backend contract change fails CI until the snapshot is refreshed (decided and added in 11.1C).
2. **Types** are generated from the snapshot into `src/types/generated/api.d.ts` by `openapi-typescript` (a dev dependency; types only, zero runtime). A test fails if the generated file is stale.
3. **Decoders** are hand-written per endpoint family from about eight tiny combinators (`object`, `array`, `nullable`, `literal`, `enumOf`, `decimalString`, `isoDate`, `isoInstant`, `uuid`), and each is declared as `Decoder<components["schemas"]["X"]>`. If the OpenAPI type changes shape, `tsc` fails at the decoder. Enum members are validated at runtime against the snapshot's enum arrays.
4. **One result type** for every call:

```
ApiResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "not_found" }                       // 404
  | { kind: "conflict"; detail: string }        // 409
  | { kind: "rejected"; issues: Issue[] }       // 422 (loc + msg, sanitised)
  | { kind: "unavailable" }                     // 503
  | { kind: "server_error"; digest?: string }   // 500 (record integrity is a known detail)
  | { kind: "unreachable" }                     // status 0 / timeout
  | { kind: "contract"; path: string }          // decoder rejected the payload
```

   It replaces `Result<T>` and `FilingsResult<T>` (F4). The error text never contains a URL (F5).
5. All API access is **server-only** (`import "server-only"`). Client components receive plain props, so there is no browser fetch and no CORS dependence.
6. Per-call timeouts: default 4 s, **10 s** for the observation and verification (F12). `cache: "no-store"` stays.

### 12.2 Runtime validation (preventing drift)

| Drift | Defence |
|---|---|
| **Enum drift** | decoders reject an unknown member (a contract failure, never a silent default); a unit test compares every TS enum table and every `StateKind` mapping to the OpenAPI snapshot, so a new backend member fails the build until it is mapped |
| **Decimal becoming a number** | `decimalString` accepts only a string matching `^-?\d+(\.\d+)?$` (no exponent, no padding); a JSON number there is a contract failure; tests inject a number for every decimal field family |
| **Missing field** | object decoders require **every documented key to be present**; the observation response is emitted with serialisation defaults required, so an absent key is drift, not "null" |
| **Nullable versus absent** | `nullable(x)` accepts explicit `null` only; absent is an error |
| **Unknown extra fields** | ignored (additive backend changes must not break the UI) |
| **Unknown status** | contract failure for single-object responses and for list pages (honesty over availability), diagnosed in development |
| **Date and instant shapes** | `YYYY-MM-DD` and ISO instants validated by regex; never parsed through `Date` for display |
| **Ids** | UUID and accession patterns validated |
| **Response echo** | as today for the filing EPS: the response must echo the requested accession and selectors; for the observation, `parameters` must echo the requested horizons, benchmark and filing; for a record, `record_id` must equal the requested id |

### 12.3 State management

URL, server fetch, small ephemeral client state and cookie preferences (section 7.2). No client store is introduced.
React `useId` for control associations; `useState` only inside the drawer, copy button, theme control and
disclosures.

### 12.4 Component taxonomy

Layers, each with a concrete reason (a layer exists only because pages need it).

| Layer | Purpose | Components (created when a second use or an accessibility need appears) |
|---|---|---|
| **L0 tokens** | one source of colour, spacing, type | `globals.css` variables |
| **L1 primitives** | accessible building blocks | `Button`, `LinkButton`, `TextField`, `SelectField`, `Callout`, `Skeleton`, `VisuallyHidden`, `Dialog` (the drawer base), `Breadcrumbs` |
| **L2 financial** | the only way to render a financial string | `FinancialValue`, `PercentValue`, `SignedDecimal`, `DateValue`, `InstantValue` (UTC), `IdText`, `FingerprintText` |
| **L3 domain honesty** | the state system | `DomainState` (inline), `RefusalNotice` (block), `StatusBadge`, `ReproducibilityBadge`, `ModeFrame` (descriptive / PIT), `NotRequested` |
| **L4 data display** | structure | `DataTable`, `Pagination`, `FilterBar`, `KeyValueList`, `StatCluster`, `PageHeader`, `EmptyState`, `ErrorState`, `EvidenceTrigger`, `EvidenceDrawer`, `CopyButton` |
| **L5 research domain** | what the product is about | `BenchmarkSelector`, `HorizonInput`, `FilingPicker`, `StudyView`, `HorizonReturnsChart`, `SessionLadder`, `HorizonReturnsTable`, `RefusalLedger`, `EpsPanels` (the existing three, refactored), `VerificationResult`, `PathList`, `ResultInspector` |
| **L6 layouts / sections** | page composition | `AppShell`, `ListPage`, `DetailPage`, `ResearchWorkspace` |

Directory layout: `src/components/{ui,financial,state,data,research,saved,layout}` and `src/lib/{api,format,state,url,chart}`.
Rules: server components by default; client components only for the theme control, copy button, drawer and the
mobile navigation. **No** Storybook, no theming library, no abstraction with a single caller (rule of two) except the
accessibility primitives. The existing `Badge` is replaced by `StatusBadge`; the existing `StatePanel` by
`EmptyState`/`ErrorState`.

### 12.5 Test strategy

Layers and what each protects (not a test-count target):

| Layer | Tool | Protects |
|---|---|---|
| **Unit** | Vitest | exact-decimal and percent formatting; decoders and combinators; the state-mapping tables (exhaustive against the OpenAPI snapshot); URL parameter parsers; chart geometry and tick generation (and that it never creates a label); the path humaniser; token contrast arithmetic |
| **Component** | Vitest + React Testing Library + user-event | `FinancialValue`/`PercentValue`, `DomainState`, `DataTable`, `Pagination`, `BenchmarkSelector` (unselected default, inactive, collision suffix), `HorizonInput` validation, `EvidenceDrawer` (focus trap, Esc, focus return), `VerificationResult` for the four statuses, chart SVG output equals the accessible table |
| **API contract / parser** | Vitest with captured backend payloads | the decoders accept real backend responses (pinned from the backend scenario fixtures) and **reject** drift: number for decimal, unknown enum, absent key, wrong echo |
| **Route / page integration** | Vitest, mocked `fetch` (the existing pattern) | each page's state matrix: ok, empty, filtered-empty, 404, 409, 422, 500, 503, unreachable, contract, and a refusal-heavy study; that a PIT refusal leaves the descriptive section intact; that no benchmark is ever sent unless chosen |
| **Accessibility** | `vitest-axe` (dev dependency) on rendered pages in both themes + user-event keyboard tests | landmarks, heading order, labels, table semantics, focus management. (jsdom cannot judge colour contrast, so contrast is guarded by the token unit test) |
| **Browser E2E** | Playwright (dev dependency, 11.1F) | six to eight smoke journeys against the real backend on a seeded test database: overview -> earnings -> event -> workspace (benchmark discovery -> selection -> results), saved verification `MATCH` and `PARTIAL`, theme switch persists, a mobile-width table scroll |

Existing tests are kept and migrated slice by slice (the filings contract and vertical tests are the model for the
rest). Each slice states the tests it must add (section 15).

### 12.6 Visual regression

**Recommendation: not now.** Screenshot suites across operating systems and fonts are brittle and low-value for a
data-dense, text-led product. The two visual risks that matter are the **charts** and the **two-theme** layouts.

- Charts are deterministic server-rendered SVG, so protect them with **SVG string snapshot tests** (unit level), which are exact and need no tooling.
- A narrow Playwright `toHaveScreenshot` suite is optional **after 11.1F**: research workspace and saved detail, light and dark, Chromium only, 1280 px, self-hosted fonts, in a fixed container. It is not part of 11.1A to 11.1F acceptance. No tooling is introduced here.

### 12.7 Performance budget and risks

Pragmatic targets, measured rather than invented. **Baseline today**: about 190 KB gzip of client JavaScript emitted
across all routes (mostly framework), six dynamic routes.

| Aspect | Target | Why / how |
|---|---|---|
| Initial client JS | no route adds more than about 15 KB gzip of application JS over the framework baseline, except the shell (theme + drawer, about 5 KB) | server components do the work; no chart or icon library; measured in 11.1B step 0 and checked per slice |
| Page transitions | shell and static regions paint without waiting on any API; data regions stream | `Suspense` per region; the backend dominates latency (a study is about a dozen queries, a verification about ten, a benchmark list two) |
| Large table | 200 rows by 12 columns renders without virtualisation | the API caps pages at 200; the existing "large page sanity" test is the model |
| Charts | no chart library; two small SVG components | server-rendered, no hydration cost |
| Saved detail | a 22 KB manifest and 17 KB result parse and render quickly | the result inspector is collapsed and renders its tree on expansion (client island receiving the JSON string) so the initial DOM stays small |

Likely risks, to be watched not assumed: **R1** server waterfalls (sequential awaits) instead of parallel reads;
**R2** the health call on every navigation (now streamed, not blocking); **R3** the keyed-`Suspense` behaviour for
`searchParams` navigations (prototype first); **R4** the per-row event enrichment on the saved list (bounded at 20);
**R5** font loading and layout shift (fallback with `size-adjust`); **R6** more than 200 benchmarks (accepted limit);
**R7** the observation payload size and the 10 s timeout on a cold database.

### 12.8 Dependencies

No new dependency is installed by this step. Recommendations:

| Need | Decision |
|---|---|
| Charts | **none**; hand-built SVG |
| Icons | **none**; about eight inline 12 px SVG glyphs authored in the repo |
| Table virtualisation | **none** (page size <= 200) |
| Date formatting | **none**; string-based formatting as today (never `Date` for data dates) |
| Fonts | `next/font/local` is built in; the Inter woff2 is a committed asset (decision D2) |
| Types from OpenAPI | `openapi-typescript` (dev dependency, 11.1C, decision D6) |
| Accessibility tests | `vitest-axe` (dev dependency, 11.1B) |
| Browser E2E | `@playwright/test` (dev dependency, 11.1F) |
| Server-only guard | `server-only` (tiny package, 11.1C) |

### 12.9 Migration and cutover

**Incremental, route by route, in one codebase.** There is no parallel V2 application and no long-lived duplicate.

- 11.1B replaces tokens, the shell and the primitives globally; the old token names remain as aliases so existing pages keep working unchanged and visually adopt the new look.
- Each later slice migrates specific routes to the new components and removes the legacy code it replaces (`Result`/`FilingsResult`, `Badge`, `StatePanel`, local sort and filter).
- The suite stays green at every slice; where the DOM changes the tests are updated in the same slice.
- There is no feature flag: a route is either migrated or not.

---

## 13. Backend to UI contract matrix

Every V2 page, its requests, the possible HTTP outcomes, the domain refusals it can show, and its empty and loading
states. All requests are server-side `GET`s. "Domain" lists the statuses that are **content**, never HTTP errors.

| Page | Requests (parallel unless noted) | Parameters sent | Response sections used | HTTP outcomes handled | Domain refusals shown | Empty | Loading |
|---|---|---|---|---|---|---|---|
| **Shell** | `GET /health` | none | `status, service, version, environment` | any failure -> "Unavailable" chip | n/a | n/a | status chip placeholder |
| **Overview** `/` | `/health/ready`; `/earnings-events?limit=8`; `/filings?limit=5`; `/research/records?limit=5` | fixed | readiness; `items, total` of each list | per panel: 503 (unavailable), 500 (records integrity), unreachable, contract | `eps_surprise.status`, `pit_eligibility` in rows; record level | no earnings / filings / baselines (section 11.2) | four independent panel skeletons |
| **Earnings** `/earnings` | `/earnings-events` | `ticker, date_from, date_to, limit=50, offset` | `EarningsEventPage` | 422 (dates), 503, 500, unreachable, contract | `eps_surprise.status` (5 values), `pit_eligibility` (4), `availability.observed_at_basis` | none / filtered-empty / past-end | table skeleton |
| **Event record** `/earnings/[eventId]` | `/earnings-events/{id}`; then (own boundary) `/research/observations/{id}` | observation: `horizons` omitted (default), no benchmark, no filing | event item; preview: `descriptive.study.alignment`, `stock_returns` | 404 and 422 -> not found; 503; 500; unreachable; contract | alignment (5), forward-return (6) statuses in the preview | no listings | header immediate; preview skeleton |
| **Filings** `/filings` | `/filings` | `form, cik, limit=50, offset` | `FilingIndexPage` | 422, 503, 500, unreachable, contract | none | none / filtered-empty / past-end | table skeleton |
| **Filing view** `/filings/[accession]` | `/filings/{accession}/eps` | `period_class, eps_kind` (required), `cik` optional | `FilingEps` (current, comparative, change) | 422, 503, 500, unreachable, contract, echo mismatch | `EpsStatus` (10), `ComparativeEpsStatus` (9), `EpsChangeStatus` (2), transitions (13), percentage reasons (2) | "Choose what to show" until both selectors set | panel skeletons |
| **Research entry** `/research` | `/earnings-events` | `ticker, date_from, date_to, limit=20, offset` | `EarningsEventPage` | as Earnings | as Earnings | as Earnings | list skeleton |
| **Workspace** `/research/events/[eventId]` | `/earnings-events/{id}`; `/benchmarks?limit=200`; `/filings?cik=<cik>&limit=50` (only when the company has a CIK); **then** `/research/observations/{id}` (result boundary) | `horizons`, `benchmark_id`, `filing_accession`, `eps_period_class`, `eps_kind` (only what the URL holds) | all of `ResearchObservationResponse`: identity, parameters, event, filing_link, disclosure_evidence, accounting (incl. `FilingEps`), descriptive, point_in_time, provenance, refusals | 404 (event), 422 (parameters), 503, 500, unreachable, contract, **echo mismatch** (`parameters` must equal the request) | listing resolution (4), alignment (5), forward return (6), benchmark return (7), abnormal return (3), EPS surprise (5), filing link (4), period verification (2), accounting state (3), disclosure level (3), section state (3), PIT state (2), PIT eligibility (4), refusal scopes (6) | no benchmarks stored -> selector shows "No benchmarks are stored"; study still runs | config immediate; result skeleton keyed by query |
| **Saved list** `/research/saved` | `/research/records`; then `/earnings-events/{id}` per distinct event (<= 20); `/benchmarks?limit=200` | `limit=20, offset` | `ResearchRecordPage` | 500 (integrity: whole list), 503, unreachable, contract; per-event 404 -> "Event no longer stored"; per-event failure -> short id | level (2), gap enum (5) | no baselines | table skeleton |
| **Saved detail** `/research/saved/[recordId]` | `/research/records/{id}`; if `verify=1`: `/research/records/{id}/verify`; `/earnings-events/{event}`; `/benchmarks?limit=200` | `record_id` (UUID); none else | `ResearchRecordDetail`; `RecordVerificationResponse` | 404, 409 (verify), 422 (malformed id -> not found page), 500 (integrity), 503, unreachable, contract | verification status (4), level, gaps | n/a | baseline immediate; verification skeleton |

Not requested by any page: `security_id` (undiscoverable, gap N3), any write verb (none exists), `limit` above 200.

---

## 14. Backend gaps seen from the UI

No backend change is requested by this design. Classification uses P0 correctness/security, P1 blocks the core V2
workflow, P2 important with a workaround, P3 nice to have.

### 14.1 The known P2/P3 items

| Gap | Class | Effect on V2 | Verdict | UI treatment |
|---|---|---|---|---|
| Record list has no `event_id` filter | P2 | no honest "saved baselines for this event" panel | **tolerable** | the panel is omitted in 11.1; **becomes blocking** only if per-event saved history is made a core workflow (needs a denormalised column) |
| No authentication or rate limiting | P2 | irrelevant to a local single-user workstation; the browser never calls the API (server-side fetch) | **tolerable** | none; **becomes blocking** before any shared or deployed use |
| No company endpoint | P2 | no Companies directory or company routes | **tolerable** | context links (section 8.2); route namespace reserved |
| No overview aggregate endpoint | P2 | no KPI dashboard | **tolerable** | list totals only (section 8.1) |
| Offset pagination can shift under concurrent writes | P3 | a row may repeat or be skipped across pages while ingestion runs | **tolerable** | one-line legend on lists |
| Source-pin ambiguity (`AMBIGUOUS_PRICE_SOURCE`) | P3 | only one price provider exists today, so it cannot occur in practice | **tolerable** | rendered as `AMBIGUOUS` with the backend reason; no remedy control exists or is invented |
| Accession lookup skip-scan | P3 | performance only on PostgreSQL <= 17 | **n/a to UI** | none |
| Earnings list does not reject repeated parameters | P3 | the UI never emits a repeat | **tolerable** | none |
| Record summary omits source pins and price basis | P3 | list rows for different pins look alike | **tolerable** | detail shows the full request; list shows the request summary only |

### 14.2 New gaps surfaced by this design audit

| ID | Gap | Class | Effect on V2 | Verdict and treatment |
|---|---|---|---|---|
| N1 | Record list and detail carry only `event_id` UUIDs: no company, ticker or event date | P2 | a saved list of bare ids is unusable | **workaround**: parallel enrichment through `/earnings-events/{id}`, deduplicated, page size 20 (D8); becomes blocking only if lists must exceed about 50 rows per page |
| N2 | **No way to save** a research baseline from the product (the service is internal) | **P2, a product decision (D1), not a blocker for 11.1B to 11.1D** | the Saved research surface lists and verifies baselines but, with no save path, it will normally be empty and its value is low | the UI says so honestly; **decide before 11.1E** between (a) a public idempotent `POST /research/records`, (b) an internal CLI save command, (c) deferring the Saved research UI. This design recommends (b) first for a local single-user product, because (a) would be the API's first write verb and there is no authentication |
| N3 | Earnings payload exposes no `security_id` or listing id | P3 | the `security_id` request knob cannot be used; a multi-security issuer's `AMBIGUOUS_LISTING` cannot be resolved by the reader | shown as `AMBIGUOUS`; no control invented |
| N4 | Observation and record payloads carry source and run **ids**, not names | P3 | provenance shows short ids except for the event source and the benchmark | tolerable; drawer says "name not available from the API" |
| N5 | Verify returns changed **paths** but no current values | P3 | the path list can show the baseline value only | tolerable; link to the live study for current values |
| N6 | No price-series endpoint | P3 (by design) | no price-path chart; only per-horizon anchor and target closes | the two charts in 8.5.6 are what the data supports |
| N7 | Gap sentences and residual-assumption text are UI copy, not API data | P3 | possible copy drift | the gap enum is parity-tested against the snapshot; the residual sentence is flagged for review whenever the backend constants change |
| N8 | The stored `result` in a record is the domain's internal structure, not the public observation shape | P2 | a saved baseline cannot be rendered with the typed `StudyView` | the result inspector (tree) is used; "Re-run live" gives the typed view; a typed saved-result contract would remove this limitation |
| N9 | Filing picker needs the company CIK, which is nullable | P3 | no filing list for a CIK-less company | manual accession entry is the fallback; a mismatch is the backend's `CIK_MISMATCH_REFUSED` |
| N10 | Benchmark list is paged at 200 | P3 | the selector cannot show more than 200 | accepted limit (R6) |

**No P0 and no P1 was found.** The only item that could change the plan is N2, and it is a decision, not a defect.

---

## 15. Implementation plan

The plan keeps the preferred shape and adjusts one dependency: the **API contract layer** moves ahead of the research
work (it is needed by every data page and by the benchmark and observation clients), and **Overview** moves to the end
because its saved-research panel depends on the records client. Each slice is shippable and leaves the suite green.

### 11.1B Foundations: tokens, theme, typography, shell, primitives

| | |
|---|---|
| Goal | the V2 look and structure for **every existing route**, with no new data and no behaviour change |
| Routes affected | all (shell and tokens are global); `/research` static page and `/` copy corrected (F7) |
| Components introduced | L0 tokens (dark + light, aliases for the old names), theme cookie + `ThemeToggle`, `AppShell` v2 (rail, 44 px header strip, streamed status, mobile navigation drawer), `Breadcrumbs`, L1 primitives (`Button`, `LinkButton`, `TextField`, `SelectField`, `Callout`, `Skeleton`, `VisuallyHidden`, `Dialog`), L2 `FinancialValue` family on top of `lib/format`, L3 state system (`DomainState`, `RefusalNotice`, `StatusBadge`, the ten states, the mapping tables for the earnings and filings enums), `EmptyState`/`ErrorState`, generic `loading.tsx`, root `not-found` |
| API endpoints consumed | the existing ones only |
| Also | remove the URL from user-visible error text (F5); drop the planned rows from navigation (D4); step 0: record the bundle baseline |
| Tests required | token contrast test computing every pair in section 10.5 from the CSS; shell, theme persistence (cookie, no flash markup) and navigation drawer component tests; state mapping exhaustiveness for the enums that exist now; `FinancialValue`/`PercentValue` unit tests (exactness, zero, negative, rounding-to-zero note, grouping); `vitest-axe` on the shell in both themes; every existing test migrated and green |
| Responsive | rail to drawer below 1024; 44 px controls below 768; verified at 1536, 1280, 1024, 768 and 390 widths |
| Accessibility | focus ring and control borders meet the thresholds (fixes F2); landmarks; skip link; drawer focus management; reduced motion |
| Deliberately deferred | any new page or data call; light-mode chart styling (no charts yet); density and direction-tint preferences (cookie plumbing is added, the UI for them waits for 11.1C) |
| Acceptance | all pages render in both themes with the new shell; zero token contrast failures; `npm run lint/typecheck/test/build` green; no new runtime dependency |

### 11.1C API contract layer, then Earnings and Filings on the V2 system

| | |
|---|---|
| Goal | one result taxonomy and drift-proof decoders; Earnings and Filings rebuilt on `DataTable` |
| Routes affected | `/earnings`, `/earnings/[eventId]` (renamed param), `/filings`, `/filings/[accession]`, `/` health and readiness reads |
| Components introduced | the OpenAPI snapshot + generated types + decoder combinators; `ApiResult` and its mapping; server-only clients for health, readiness, earnings, filings; `DataTable`, `Pagination`, `FilterBar`, `KeyValueList`, `PageHeader` v2; the event record's V2 layout (without the market-reaction preview, which needs the observation client); `EpsPanels` refactored to the state system; company context links |
| API endpoints consumed | health, readiness, earnings list and detail, filings index, filing EPS |
| Backend-side (test only) | a backend test asserting the committed OpenAPI snapshot equals `app.openapi()` (no source change) |
| Tests required | decoder acceptance of pinned real payloads and **rejection** of drift (number for a decimal, unknown enum, absent key, wrong echo); the full page state matrix for the four pages (ok, empty, filtered-empty, past-end, 404, 422, 503, 500, unreachable, contract); pagination and URL round-trips; removal of the page-local sort/filter tests; generated-types staleness test; `vitest-axe` on all four pages |
| Responsive | tables scroll with a sticky identity column; P3/P2 column hiding; verified widths as above |
| Accessibility | table semantics, scroll-region labels, state exposure in cell accessible names, form error association |
| Deliberately deferred | observation, benchmarks and records clients; charts; the market-reaction preview |
| Acceptance | one result type everywhere; legacy `Result`/`FilingsResult`/`Badge`/`StatePanel` deleted; all earnings and filings behaviour that existed is preserved except decision D3; contract tests demonstrably fail on injected drift |

### 11.1D Research workspace

| | |
|---|---|
| Goal | the core product surface: select an event, optionally a benchmark, run and read a study honestly |
| Routes affected | `/research` (event finder), `/research/events/[eventId]`, `/earnings/[eventId]` (market-reaction preview + primary action) |
| Components introduced | observation and benchmarks decoders and clients; URL parsers (`horizons`, ids, selectors); `BenchmarkSelector`, `HorizonInput`, `FilingPicker`, `ModeFrame`, `StudyView`, `HorizonReturnsChart`, `SessionLadder`, `HorizonReturnsTable`, `RefusalLedger`, result status strip, `EvidenceTrigger` + `EvidenceDrawer` (first use), `NotRequested` handling; the keyed-`Suspense` result region |
| API endpoints consumed | earnings detail and list, benchmarks, filings (picker), research observation |
| Tests required | selector (unselected default, inactive, collisions, unknown id); horizon validation; **no benchmark/filing is ever sent unless in the URL**; echo-mismatch is a contract error; a PIT `REFUSED` study leaves every descriptive element intact; each refusal status renders its mapped state; abnormal-return rows are `NOT_REQUESTED` (not `UNAVAILABLE`) without a benchmark; chart SVG snapshot tests and chart-equals-table tests; the geometry module never emits a label; drawer focus tests; the page state matrix; `vitest-axe` in both themes |
| Responsive | configuration rail to disclosure below 1280; inspector docks at 1536; chart and table at narrow widths; verified |
| Accessibility | chart `<title>/<desc>`, focusable groups, equivalent table; live announcements; form errors |
| Deliberately deferred | saved-research integration, save action, `security_id`, price-path charts |
| Acceptance | a reader can go from the earnings list to a benchmark-relative study for an event, change horizons and benchmark by URL, and understand every refusal on the page; prototype confirms R3 before the slice is closed |

### 11.1E Saved research, verification and provenance

| | |
|---|---|
| Goal | list, open and verify saved baselines; make reproducibility legible |
| Precondition | decision **D1** (how baselines are created) recorded; the slice itself is read-only either way |
| Routes affected | `/research/saved`, `/research/saved/[recordId]` |
| Components introduced | records decoders and clients; `ReproducibilityBadge`, gap sentences, `VerificationResult`, `PathList`, `ResultInspector`, event/benchmark enrichment (parallel, bounded), `EvidenceDrawer` extended (fingerprints) |
| API endpoints consumed | records list, record detail, record verify, earnings detail, benchmarks |
| Tests required | all four verification statuses and their copy; path grouping/humanising and the 100-path cap; list integrity `500` is not an empty list; enrichment 404 vs failure; `?verify=1` rerun semantics; fingerprints shortened and copyable; empty-state copy; `vitest-axe` |
| Responsive / accessibility | list table rules; result inspector keyboard operable (tree semantics); status messages announced |
| Deliberately deferred | a save action; a typed saved-result renderer (gap N8); per-event saved history (gap P2) |
| Acceptance | a stored baseline can be opened, verified, and its outcome understood without reading raw JSON; `PARTIAL` is never presented as an error |

### 11.1F Overview, company context, polish and end-to-end

| | |
|---|---|
| Goal | the launchpad, the context links everywhere, the polish, and the safety net |
| Routes affected | `/` and a sweep of all routes |
| Components introduced | Overview panels (independent `Suspense`), the ticker entry, `CopyButton` where missing, density and direction-tint preference UI; Playwright harness |
| API endpoints consumed | all list endpoints with small limits |
| Tests required | Overview per-panel failure isolation; the responsive and accessibility sweep (manual keyboard checklist + `vitest-axe`); **6 to 8 Playwright journeys** against a seeded backend; performance check against the budget in 12.7 |
| Deliberately deferred | Companies directory, per-event saved history, visual regression, backtests |
| Acceptance | every surface in the design is reachable and honest at the four breakpoints; budgets recorded; documentation (web README, root README, this spec) updated to the as-built state |

### Approval gates

Before 11.1B starts: approve the open decisions D2, D4 and D7 (section 16.2). Before 11.1C: D3 and D6. Before 11.1E: D1.

---

## 16. Design review

### 16.1 Questions answered

| Question | Answer |
|---|---|
| What is QuantForge's primary user workflow? | Find an earnings event, read its stored evidence, configure a study (optional benchmark, horizons, optional filing), read the descriptive result and every refusal, inspect provenance, and compare against a saved baseline. |
| What is the default landing page? | `/` Overview, a launchpad built only from existing list endpoints and readiness, with no KPIs. |
| Should Companies exist as a top-level page today? | **No.** There is no company endpoint; a crawled directory would be false completeness. Company identity is shown as context with links to `/earnings?ticker=` and `/filings?cik=`; `/companies` is reserved. |
| What belongs in persistent research context? | **Nothing implicit.** Only theme, density and direction tint persist (cookie). Event, benchmark, horizons, filing and EPS selectors live in the URL and are never remembered or defaulted. |
| What is the primary Research page layout? | A sticky configuration rail beside a result column that opens with a status strip, then the stored event, the descriptive frame, the point-in-time frame, accounting and filing, the refusal ledger and provenance. |
| What is the primary chart? | The **return-by-horizon dumbbell** (stock, benchmark, and the abnormal-return gap per horizon) with a **session ladder** for alignment, each with an equivalent table. |
| How are descriptive and PIT results distinguished? | Separate frames with different labels, solid versus dashed rules, different hues and state vocabularies, a status strip showing both states at once, and a fixed sentence that a PIT refusal does not affect the descriptive study. |
| How is provenance accessed? | An `EvidenceTrigger` next to a value group opens a modal `EvidenceDrawer` (docked at 1536 px and above); a collapsed Provenance section lists everything. |
| How is reproducibility communicated? | A two-value badge (Complete, Partial), the named gaps with plain sentences, the residual assumptions on every saved record, and no level at all for an unsaved study. Partial is never an error. |
| Which backend gaps affect V2? | N1 (event label on records), N2 (no save path, a decision), N3 (no `security_id`), N5, N8, and the known P2 set. None is P0 or P1. |
| Which do NOT justify backend work? | Authentication and rate limiting (local product), the company and overview endpoints, the record `event_id` filter, offset-pagination race, source-pin ambiguity, the accession skip-scan, the earnings repeated-parameter inconsistency, record-summary source pins, N3, N4, N5, N6, N7, N9, N10. |
| What should be implemented first? | **11.1B Foundations**: tokens (dark and light), theme, typography, the shell and the primitives, including the state system. |

### 16.2 Decisions needing human approval

| ID | Decision | Recommendation |
|---|---|---|
| D1 | How baselines get saved (public `POST`, internal CLI, or defer Saved research) | internal CLI first; a public `POST` only with an authentication story. **Deferred by the 11.1A approval; decide before 11.1E** |
| D2 | Self-hosted Inter vs the system font stack | self-hosted Inter via `next/font/local`; system stack acceptable |
| D3 | Remove the page-local sort and filter from the earnings table | remove |
| D4 | Drop "Planned" rows (Companies, Backtests) from navigation | drop; one "Not available yet" note on Overview |
| D5 | Verification as a `?verify=1` server-driven link rather than a client fetch | link (no client fetch, no CORS, shareable, honest about "rerun on load") |
| D6 | `openapi-typescript` as the one generated-types dependency | accept |
| D7 | Direction tint default off | off |
| D8 | Saved list page size 20 (to bound per-row enrichment) | 20 |

### 16.3 Remaining design risks

| Risk | Mitigation |
|---|---|
| R3 `searchParams`-only navigations may not re-show the skeleton | key the result `Suspense` by canonical query; prototype in 11.1D before building on it |
| N2 leaves Saved research mostly empty | decide D1 early; the empty state is honest regardless |
| A large surface (state system, charts, drawer) in few slices | 11.1B/C carry the foundations; 11.1D/E consume them; rule of two limits premature components |
| Contract drift between backend and UI | snapshot + generated types + strict decoders + enum parity tests |
| Hand-built SVG charts need careful accessibility | equivalent tables, `<title>/<desc>`, SVG snapshot tests |
| Font and theme flash | cookie read on the server, `size-adjust` fallback |
| The observation can be slow on a cold database | 10 s timeout, streamed skeleton, clear unreachable/503 states |

---

---

## Implementation notes: Step 11.1C

Where the shipped 11.1C differs from, or settles, this document (the repository and the generated OpenAPI win):

1. **Ticker column and filter.** The event payload carries `resolved_tickers` (tickers the backend's historical
   resolver proves for the event date). The Earnings table and the event record use it, not
   `listings[].valid_on_event_date`; the listings table still shows listing validity.
2. **Breadcrumbs** are page content, not part of the header strip: the shell is rendered once by the layout and
   cannot know a page's data. The header strip shows the current section (`Evidence / Earnings`) from the location.
3. **Filings table column order.** The accession (the identity link) is the first, sticky column, so the link is in
   the identity cell as section 10.7.1 requires; the filing date follows the form and CIK.
4. **Market-reaction preview** (section 8.3.2) is deferred to 11.1D with the observation client, as the 11.1C
   slice table already says. The event record shows no placeholder for it.
5. **`/research/events/[eventId]`** exists as a plain placeholder so "Open research workspace" never leads to a
   dead link. It makes no request and shows nothing computed.
6. **`server-only`** was not added: an import-graph test proves no client component can reach the API client,
   the decoders or the configuration.
7. **Conflict (409)** carries no response text; the slice that needs the backend's fixed sentence allow-lists it.
8. **Timeouts.** Only the 4 s read class is used; the 10 s class arrives with the computed endpoints.
9. **Searchparam navigation.** A list page keys its `Suspense` by the canonical address; observed in a browser, a
   client navigation that changes only the query string replays the skeleton and does not leave old rows under new
   filters. (Form submission is a full navigation.)
10. **Status chip.** It is computed once per document load; navigation keeps it mounted. Refreshing it on
    navigation (polling) is not part of 11.1C.

---

## Implementation notes: Step 11.1D

Where the shipped research workspace differs from, or settles, this document (the repository and the generated
OpenAPI win):

1. **Filing picker rejected.** Section 8.5.2 proposed a select of the company's stored filings from
   `/filings?cik=`. The filing index lists only FSDS-backed filings: 8-K filings (the only source of Item 2.02
   disclosure evidence) are absent, so a picker would present an incomplete candidate set and suggest a
   relationship. The workspace takes an explicit accession (validated) and offers a plain "Filings for this
   registrant" lookup link that claims no relationship. The workspace never calls the filing index.
2. **Benchmark selector pages the index** (`bpage`, 200 per page, "Showing benchmarks a-b of N, one page of the
   index, not a search") instead of listing "the first 200"; the selected benchmark is named from the page the
   selector is on, otherwise by id. Inactive benchmarks are listed (own group) and selectable.
3. **Refusal codes are scope-typed.** `RefusalOut.code` is free text in the OpenAPI schema, but each scope draws
   from known enums; the decoder rejects a code outside its scope's vocabulary.
4. **Chart.** A categorical dumbbell per horizon (filled circle stock, hollow diamond benchmark, the backend's gap
   as the connector); one chart per mode study; no event marker on a time axis (the session ladder places the
   event between session slots by declared timing). The y axis has headroom beyond the largest value.
5. **Preview on the event record** requests the descriptive default study (no benchmark, no filing) in its own
   boundary, so each event-record view costs one observation read (about a dozen stored reads).
6. **No reproducibility level, verification or save** anywhere in the live workspace.
7. **Context bar price basis** is a fixed statement ("split-adjusted, the only basis research reads"); the
   observation's `price_basis` must echo it (anything else is a contract failure).
8. **Ticker** on the research context comes from the earnings event record (`resolved_tickers`); the observation
   carries no ticker.

## Implementation notes: Step 11.1E

Where the shipped Saved Research differs from, or settles, this document (the repository and the generated OpenAPI
win):

1. **Read-only, by decision (D1).** No save, no save-shaped affordance and no disabled placeholder exists; a future
   save workflow is designed together with authentication, ownership and authorization.
2. **The list carries no event identity** (only the request's `event_id`). The event column is therefore the CURRENT
   event record, one read per distinct event on the page (at most 20), labelled "Event (current record)" and
   explained under the table. A 404 or failure shows in that cell only.
3. **The stored result is the backend's canonical serialisation, not the live response.** The OpenAPI schema types
   `manifest` and `result` as open objects; their shape (nested per-horizon items, no presenter fields, no section
   state, no refusal ledger) differs from the live observation. Decoders pin the shape; adapters restructure it for
   the shared study components; no state or ledger is invented for a stored result.
4. **No residual-assumptions list.** The API does not supply one for a record, so none is shown or invented.
5. **No raw result inspector.** The semantic sections cover every decoded field; a pretty-printed dump would add
   tens of kilobytes to every document for fields the page does not interpret.
6. **Verification entry is a link** (`?verify=1#verification`), not a button, so it is shareable and survives
   refresh and back/forward. The verification decoder refuses a report whose parts contradict its status; it never
   assigns one.
7. **Fingerprints** are shortened in the list (full value in the document and the hover title) and shown whole, with
   a copy action, on the record. The record detail supplies only the manifest and result fingerprints; the other
   three (request, methodology, evidence) appear in a verification report, side by side, with no sameness mark.
8. **Re-run live** carries only parameters the workspace accepts and says when the stored request pinned something
   it cannot carry (a security or a price source).

## Implementation notes: Step 11.1F

The convergence slice. Where Frontend V2 as shipped differs from, or settles, this document:

1. **Overview is a launchpad.** Start research, latest earnings events, latest filings, recent saved research and the
   API's health, each an independent bounded list read (five rows) in its own boundary. No KPI, aggregate, count of
   its own or per-row enrichment. Companies and Backtests are not mentioned: a section that does not exist is not shown.
2. **One name per concept** (the vocabulary the product now uses everywhere): a saved item is a *saved research
   record*; what it holds is a *stored baseline*; the live computation is a *research result* (never a baseline); an
   earnings row is an *event*; the column is *Point-in-time*; the link into the workspace is *Research* /
   *Open Research*. Page titles read `{identity} · {surface}`.
3. **One badge per status.** The point-in-time classification is one component on four surfaces; verification,
   reproducibility and mode badges are unchanged. Redundant badges were removed (the earnings availability frame
   showed the same verdict twice).
4. **Production link prefetching is off** for every link except the primary navigation (see the README). This was
   found by the browser tests, not by the unit tests or `next dev` (which never prefetches): an Overview of five events
   made fifteen API requests in production. Budgets per surface are pinned.
5. **Accessibility found by a real browser**, where jsdom cannot measure: links inside running text were told apart
   by colour alone (WCAG 1.4.1, 1.03 to 1.6:1 against the sentence) and the horizon presets were 5.6 by 18 px targets
   (WCAG 2.5.8). Prose links are now underlined (a zero-specificity rule in the components layer) and the presets are
   at least 24 by 24 px. A state that is itself the page (not-found, the error boundary) now carries the page's one h1.
6. **Deliberately deferred, with the evidence:**
   - **Companies**: not added to make the navigation look complete; company context continues through event and
     filing links.
   - **Save, authentication, ownership**: unchanged from D1.
   - **Density preference**: not implemented. The final V2 was reviewed at 1440, 1024, 390 and 320 px in both
     themes; tables are already dense by design (12 px, a 1.35 line height, priority columns, a labelled scroll
     region), touch targets are 44 px below 1024 px, and nothing was cramped or wasteful enough to justify a control.
   - **Direction tint**: not implemented as a control. Every signed figure carries its sign in the text, the tables
     read clearly without a hue, and the default stays restrained. The server-readable cookie and its CSS hook from
     11.0 remain (inert without the cookie, tested).
   - **Status refresh**: kept per document load. Browser testing (`e2e/shell-status.spec.ts`) shows the chip says,
     in its own tooltip, when it was checked; a client navigation does not re-check it, and a page whose data fails
     says so itself, so a stale "Connected" never leaves a failure unexplained. Polling or global client state was
     not introduced to make it "live".
7. **Fixture-pinned vocabulary.** `EVIDENCE_STATES` (NOT_REQUESTED, NOT_READ, PINNED) cannot be derived from the
   OpenAPI because a record's manifest is an open object; it is pinned by the decoder and the captured manifests, and
   a test says so. No backend schema was changed for it.

---

---

## Appendix A. Endpoint contracts (generated from the OpenAPI at the base commit)

Generated mechanically from `app.openapi()`; the committed snapshot of section 12.1 will replace this appendix as the authority. `DecimalText` fields are JSON strings.

### GET `/health`

_Liveness probe_

Responses: `200`.

200 body `HealthResponse`: status: string; service: string; version: string; environment: string


### GET `/api/v1/health`

_Liveness probe_

Responses: `200`.

200 body `HealthResponse`: status: string; service: string; version: string; environment: string


### GET `/api/v1/health/ready`

_Readiness probe_

Responses: `200`, `503`.

200 body `ReadinessResponse`: status: string; service: string; dependencies: DependencyReadiness; detail: object | null


### GET `/api/v1/earnings-events`

_List earnings events_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `limit` | query | no | integer; minimum=1; maximum=200; default=50 |
| `offset` | query | no | integer; minimum=0; maximum=1000000; default=0 |
| `ticker` | query | no | string | null; pattern=^\S(.*\S)?$; maxLength=32 |
| `date_from` | query | no | string(date) | null |
| `date_to` | query | no | string(date) | null |

Responses: `200`, `422`.

200 body `EarningsEventPage`: items: list[EarningsEventItem]; total: integer; limit: integer; offset: integer


### GET `/api/v1/earnings-events/{event_id}`

_Get one earnings event_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `event_id` | path | yes | string(uuid) |

Responses: `200`, `404`, `422`.

200 body `EarningsEventItem`: id: string(uuid); company: CompanyRef; source: SourceRef; listings: list[ListingRef]; resolved_tickers: list[string]; fiscal_year: integer; fiscal_quarter: integer | null; event_date: string(date); event_timestamp: string(date-time) | null; timing: EarningsTiming; currency: string | null; reported_eps: string | null; estimated_eps: string | null; reported_revenue: string | null; estimated_revenue: string | null; eps_surprise: EpsSurprise; availability: Availability; ingested_at: string(date-time)


### GET `/api/v1/benchmarks`

_Benchmarks a caller can explicitly select_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `limit` | query | no | integer; minimum=1; maximum=200; default=50 |
| `offset` | query | no | integer; minimum=0; maximum=1000000; default=0 |

Responses: `200`, `422`, `503`.

200 body `BenchmarkIndexPage`: items: list[BenchmarkIndexItem]; total: integer; limit: integer; offset: integer


### GET `/api/v1/filings`

_Index of stored filings with FSDS financial-fact data_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `limit` | query | no | integer; minimum=1; maximum=200; default=50 |
| `offset` | query | no | integer; minimum=0; maximum=1000000; default=0 |
| `form` | query | no | SecFormType | null |
| `cik` | query | no | string | null; pattern=^[0-9]{10}$ |

Responses: `200`, `422`, `503`.

200 body `FilingIndexPage`: items: list[FilingIndexItem]; total: integer; limit: integer; offset: integer


### GET `/api/v1/filings/{accession}/eps`

_Current EPS, comparative EPS and EPS change for one filing_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `accession` | path | yes | string; pattern=^[0-9]{10}-[0-9]{2}-[0-9]{6}$ |
| `period_class` | query | yes | EpsPeriodClass |
| `cik` | query | no | string | null; pattern=^[0-9]{10}$ |
| `eps_kind` | query | yes | EpsKind |

Responses: `200`, `422`, `503`.

200 body `FilingEpsResponse`: accession: string; company_cik: string | null; form: SecFormType | null; period_class: EpsPeriodClass; eps_kind: EpsKind; descriptive_only: boolean; no_point_in_time_claim: boolean; boundary_note: string; current: CurrentEpsSection; comparative: ComparativeEpsSection; change: EpsChangeSection


### GET `/api/v1/research/observations/{event_id}`

_One research observation for one earnings event_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `event_id` | path | yes | string(uuid) |
| `horizons` | query | no | string | null; maxLength=64 |
| `benchmark_id` | query | no | string(uuid) | null |
| `security_id` | query | no | string(uuid) | null |
| `filing_accession` | query | no | string | null; pattern=^[0-9]{10}-[0-9]{2}-[0-9]{6}$ |
| `eps_period_class` | query | no | EpsPeriodClass | null |
| `eps_kind` | query | no | EpsKind | null |

Responses: `200`, `404`, `422`, `503`.

200 body `ResearchObservationResponse`: identity: IdentityOut; parameters: ObservationParametersOut; event: EventOut; filing_link: FilingLinkOut; disclosure_evidence: DisclosureEvidenceOut; accounting: AccountingOut; descriptive: DescriptiveOut; point_in_time: PointInTimeOut; provenance: ProvenanceOut; refusals: list[RefusalOut]


### GET `/api/v1/research/records`

_Saved research records_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `limit` | query | no | integer; minimum=1; maximum=200; default=50 |
| `offset` | query | no | integer; minimum=0; maximum=1000000; default=0 |

Responses: `200`, `422`, `500`, `503`.

200 body `ResearchRecordPage`: items: list[ResearchRecordSummary]; total: integer; limit: integer; offset: integer


### GET `/api/v1/research/records/{record_id}`

_One saved research record_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `record_id` | path | yes | string(uuid) |

Responses: `200`, `404`, `422`, `500`, `503`.

200 body `ResearchRecordDetail`: record_id: string(uuid); created_at: string(date-time); hash_algorithm: string; manifest_fingerprint: string; result_fingerprint: string; reproducibility_level: ReproducibilityLevel; reproducibility_gaps: list[ReproducibilityGap]; request: RecordRequestSummary; manifest: object; result: object


### GET `/api/v1/research/records/{record_id}/verify`

_Re-verify one saved research record against current stored evidence_

| Parameter | In | Required | Schema |
|---|---|---|---|
| `record_id` | path | yes | string(uuid) |

Responses: `200`, `404`, `409`, `422`, `500`, `503`.

200 body `RecordVerificationResponse`: record_id: string(uuid); status: string; baseline: string; reproducibility_level: ReproducibilityLevel; changed_sections: list[string]; changed_paths: list[string]; changed_result_paths: list[string]; expected: RecordVerificationSide; actual: RecordVerificationSide


### Nested schemas of the research observation and records

- **ResearchObservationResponse**: identity: IdentityOut; parameters: ObservationParametersOut; event: EventOut; filing_link: FilingLinkOut; disclosure_evidence: DisclosureEvidenceOut; accounting: AccountingOut; descriptive: DescriptiveOut; point_in_time: PointInTimeOut; provenance: ProvenanceOut; refusals: list[RefusalOut]
- **IdentityOut**: company_id: string(uuid); company_cik: string | null; event_id: string(uuid); listing_resolution: ListingResolutionOut; benchmark_id: string(uuid) | null; filing_accession: string | null
- **ObservationParametersOut**: horizons: list[integer]; price_basis: PriceBasis; security_id: string(uuid) | null; benchmark_id: string(uuid) | null; filing_accession: string | null; eps_period_class: EpsPeriodClass | null; eps_kind: EpsKind | null
- **ListingResolutionOut**: status: ListingResolutionStatus; listing_id: string(uuid) | null; security_id: string(uuid) | null; exchange_mic: string | null; evidence: Evidence | null; reason: string
- **EventOut**: event_id: string(uuid); fingerprint: string; fiscal_year: integer; fiscal_quarter: integer | null; event_date: string(date); event_timestamp: string(date-time) | null; timing: EarningsTiming; reported_eps: string | null; estimated_eps: string | null; observed_at: string(date-time) | null; observed_at_basis: AvailabilityBasis | null
- **FilingLinkOut**: status: FilingLinkStatus; period_verification: PeriodVerification; accession_number: string | null; company_cik: string | null; form: SecFormType | null; reason: string
- **DisclosureEvidenceOut**: level: DisclosureEvidenceLevel; accession_number: string | null; form: SecFormType | null; items: string | null
- **AccountingOut**: state: AccountingState; eps: FilingEpsResponse | null
- **DescriptiveOut**: state: SectionState; study: StudyOut
- **PointInTimeOut**: state: PointInTimeState; eligibility: PointInTimeEligibility; study: StudyOut | null
- **StudyOut**: research_mode: ResearchMode; point_in_time_eligibility: PointInTimeEligibility; eps_surprise: EpsSurpriseOut; alignment: AlignmentOut; stock_returns: StockReturnsOut; benchmark: BenchmarkOut; abnormal_returns: list[AbnormalReturnOut]; price_basis_note: string
- **EpsSurpriseOut**: status: EpsSurpriseStatus; reported_eps: string | null; estimated_eps: string | null; absolute_surprise: string | null; surprise_ratio: string | null; reason: string
- **AlignmentOut**: event_date: string(date); timing: EarningsTiming; session_alignment: SessionAlignmentOut | null
- **SessionAlignmentOut**: status: AlignmentStatus; event_session: string(date) | null; first_post_event_session: string(date) | null; reason: string
- **StockReturnsOut**: listing_id: string(uuid) | null; price_source_id: string(uuid) | null; anchor_session: string(date) | null; forward_returns: list[ForwardReturnOut]
- **ForwardReturnOut**: horizon: integer; status: ForwardReturnStatus; anchor_session: string(date) | null; anchor_close: string | null; target_session: string(date) | null; target_close: string | null; return_value: string | null; reason: string
- **BenchmarkOut**: benchmark_id: string(uuid) | null; returns: list[BenchmarkReturnOut]
- **BenchmarkReturnOut**: horizon: integer; status: BenchmarkReturnStatus; anchor_session: string(date) | null; anchor_close: string | null; target_session: string(date) | null; target_close: string | null; benchmark_return: string | null; reason: string
- **AbnormalReturnOut**: horizon: integer; status: AbnormalReturnStatus; abnormal_return: string | null; reason: string
- **ProvenanceOut**: event_fingerprint: string; event_source_id: string(uuid); event_ingestion_run_id: string(uuid) | null; stock_price_source_id: string(uuid) | null; benchmark_id: string(uuid) | null; filing_source_id: string(uuid) | null; filing_ingestion_run_id: string(uuid) | null; accounting_artifacts: list[ArtifactDigestOut]
- **ArtifactDigestOut**: role: string; sha256: string
- **RefusalOut**: scope: RefusalScope; code: string; reason: string; horizon: integer | null
- **FilingEpsResponse**: accession: string; company_cik: string | null; form: SecFormType | null; period_class: EpsPeriodClass; eps_kind: EpsKind; descriptive_only: boolean; no_point_in_time_claim: boolean; boundary_note: string; current: CurrentEpsSection; comparative: ComparativeEpsSection; change: EpsChangeSection
- **ResearchRecordSummary**: record_id: string(uuid); created_at: string(date-time); hash_algorithm: string; manifest_fingerprint: string; result_fingerprint: string; reproducibility_level: ReproducibilityLevel; reproducibility_gaps: list[ReproducibilityGap]; request: RecordRequestSummary
- **RecordRequestSummary**: event_id: string(uuid); horizons: list[integer]; benchmark_id: string(uuid) | null; security_id: string(uuid) | null; filing_accession: string | null; eps_period_class: string | null; eps_kind: string | null
- **ResearchRecordDetail**: record_id: string(uuid); created_at: string(date-time); hash_algorithm: string; manifest_fingerprint: string; result_fingerprint: string; reproducibility_level: ReproducibilityLevel; reproducibility_gaps: list[ReproducibilityGap]; request: RecordRequestSummary; manifest: object; result: object
- **RecordVerificationResponse**: record_id: string(uuid); status: string; baseline: string; reproducibility_level: ReproducibilityLevel; changed_sections: list[string]; changed_paths: list[string]; changed_result_paths: list[string]; expected: RecordVerificationSide; actual: RecordVerificationSide
- **RecordVerificationSide**: reproducibility_level: ReproducibilityLevel; reproducibility_gaps: list[ReproducibilityGap]; fingerprints: RecordFingerprints
- **RecordFingerprints**: request: string; methodology: string; evidence: string; manifest: string; result: string
- **BenchmarkIndexItem**: id: string(uuid); name: string; symbol: string | null; currency: string | null; is_active: boolean
- **EarningsEventItem**: id: string(uuid); company: CompanyRef; source: SourceRef; listings: list[ListingRef]; resolved_tickers: list[string]; fiscal_year: integer; fiscal_quarter: integer | null; event_date: string(date); event_timestamp: string(date-time) | null; timing: EarningsTiming; currency: string | null; reported_eps: string | null; estimated_eps: string | null; reported_revenue: string | null; estimated_revenue: string | null; eps_surprise: EpsSurprise; availability: Availability; ingested_at: string(date-time)
- **FilingIndexItem**: accession: string; company_cik: string | null; form: SecFormType; filing_date: string(date); report_date: string(date) | null; fsds_period_end: string(date) | null

### Enumerations (all response vocabularies)

- **AbnormalReturnStatus**: `AVAILABLE`, `STOCK_RETURN_UNAVAILABLE`, `BENCHMARK_RETURN_UNAVAILABLE`
- **AccountingState**: `NOT_REQUESTED`, `LINK_REFUSED`, `EVALUATED`
- **AlignmentStatus**: `ALIGNED`, `UNKNOWN_TIMING`, `INTRADAY_PRECISION_UNSUPPORTED`, `NO_SESSION_DATA`, `NOT_POINT_IN_TIME_ELIGIBLE`
- **AvailabilityBasis**: `SOURCE_PUBLICATION`, `SEC_FILING_ACCEPTANCE`
- **BenchmarkReturnStatus**: `AVAILABLE`, `NO_BENCHMARK`, `NO_STOCK_RETURN`, `MISSING_ANCHOR_PRICE`, `MISSING_TARGET_PRICE`, `INVALID_PRICE`, `AMBIGUOUS_BENCHMARK_PRICE_SOURCE`
- **ComparativeEpsStatus**: `AVAILABLE`, `CURRENT_UNAVAILABLE`, `COMPARATIVE_MISSING`, `COMPARATIVE_PERIOD_MISMATCH`, `COMPARATIVE_DIMENSIONAL_ONLY`, `COMPARATIVE_COREG_ONLY`, `COMPARATIVE_WRONG_UNIT`, `COMPARATIVE_NULL_VALUE`, `COMPARATIVE_AMBIGUOUS`
- **DisclosureEvidenceLevel**: `NONE`, `D0_FILING_EXISTS`, `D1_ITEM_2_02_DECLARED`
- **EarningsTiming**: `BEFORE_MARKET`, `DURING_MARKET`, `AFTER_MARKET`, `UNKNOWN`
- **EpsChangeStatus**: `AVAILABLE`, `INPUT_UNAVAILABLE`
- **EpsKind**: `DILUTED`, `BASIC_AND_DILUTED`
- **EpsPeriodClass**: `QUARTER`, `ANNUAL`
- **EpsStatus**: `AVAILABLE`, `NOT_INGESTED`, `UNSUPPORTED_FORM_PERIOD`, `MISSING`, `EXTENSION_ONLY`, `WRONG_UNIT`, `DIMENSIONAL_ONLY`, `COREG_ONLY`, `NULL_VALUE`, `AMBIGUOUS`
- **EpsSurpriseStatus**: `AVAILABLE`, `MISSING_REPORTED_EPS`, `MISSING_ESTIMATED_EPS`, `ZERO_ESTIMATE`, `NOT_POINT_IN_TIME_ELIGIBLE`
- **EpsTransition**: `POSITIVE_INCREASED`, `POSITIVE_DECREASED`, `POSITIVE_UNCHANGED`, `LOSS_NARROWED`, `LOSS_WIDENED`, `LOSS_UNCHANGED`, `LOSS_TO_PROFIT`, `PROFIT_TO_LOSS`, `ZERO_TO_PROFIT`, `ZERO_TO_LOSS`, `PROFIT_TO_ZERO`, `LOSS_TO_ZERO`, `ZERO_UNCHANGED`
- **Evidence**: `PROVISIONAL_FIRST_OBSERVED`, `SOURCE_STATED_HISTORY`
- **FilingLinkStatus**: `NOT_LINKED`, `FILING_NOT_STORED`, `CIK_MISMATCH_REFUSED`, `CALLER_SUPPLIED_CIK_CONSISTENT`
- **ForwardReturnStatus**: `AVAILABLE`, `NO_ALIGNMENT`, `MISSING_ANCHOR_PRICE`, `INSUFFICIENT_FORWARD_SESSIONS`, `MISSING_TARGET_PRICE`, `INVALID_PRICE`, `AMBIGUOUS_PRICE_SOURCE`
- **ListingResolutionStatus**: `RESOLVED`, `NO_LISTING_FOR_DATE`, `AMBIGUOUS_LISTING`, `CONFLICTING_HISTORY`
- **PercentageUnavailableReason**: `ZERO_COMPARATIVE`, `NEGATIVE_COMPARATIVE`
- **PeriodVerification**: `NOT_APPLICABLE`, `PERIOD_UNVERIFIED`
- **PointInTimeEligibility**: `NOT_APPLICABLE`, `ELIGIBLE`, `INELIGIBLE_NO_AVAILABILITY_EVIDENCE`, `INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS`
- **PointInTimeState**: `REFUSED`, `EVIDENCE_ELIGIBLE`
- **PriceBasis**: `SPLIT_ADJUSTED`, `AS_TRADED`
- **ProviderType**: `REGULATORY_FILING`, `EXCHANGE_FEED`, `MARKET_DATA_VENDOR`, `BROKER_API`, `MANUAL`, `OTHER`
- **RefusalScope**: `EVENT`, `MARKET`, `BENCHMARK`, `FILING_LINK`, `ACCOUNTING`, `POINT_IN_TIME`
- **ReproducibilityGap**: `EVENT_WITHOUT_INGESTION_RUN`, `STOCK_BAR_WITHOUT_INGESTION_RUN`, `BENCHMARK_BARS_WITHOUT_INGESTION_RUN`, `FILING_WITHOUT_INGESTION_RUN`, `ACCOUNTING_NOT_INGESTED`
- **ReproducibilityLevel**: `COMPLETE`, `PARTIAL`
- **ResearchMode**: `DESCRIPTIVE`, `POINT_IN_TIME`
- **SecFormType**: `10-K`, `10-K/A`, `10-Q`, `10-Q/A`, `8-K`, `8-K/A`
- **SectionState**: `AVAILABLE`, `PARTIAL`, `UNAVAILABLE`
- **SecurityType**: `EQUITY`, `PREFERRED_EQUITY`, `DEPOSITORY_RECEIPT`, `ETF`, `MUTUAL_FUND`, `BOND`, `OTHER`

Inline vocabularies not listed as named enums: record verification `status` is one of `MATCH`, `MANIFEST_CHANGED`, `RESULT_CHANGED`, `EVIDENCE_INCOMPLETE`; `baseline` is `SUPPLIED` or `RERUN` (always `SUPPLIED` for the stored-record endpoint); `changed_sections` items are `request`, `methodology` or `evidence`; the record request summary types `eps_period_class` and `eps_kind` as plain strings; `hash_algorithm` is `sha256`.

---

## Appendix B. Status to state mapping

Implemented as exhaustive `Record<Status, StateKind>` tables. Where the UI needs context beyond one status (for
example abnormal return depends on the benchmark status of the same horizon) the rule is stated.

`V` = `VALUE`, `NS` = `NOT_STATED`, `NR` = `NOT_REQUESTED`, `NA` = `NOT_APPLICABLE`, `M` = `MISSING`, `U` = `UNAVAILABLE`,
`R` = `REFUSED`, `A` = `AMBIGUOUS`, `P` = `PARTIAL`.

| Vocabulary | Status | State | Note |
|---|---|---|---|
| `AlignmentStatus` | `ALIGNED` | V | |
| | `UNKNOWN_TIMING` | R | the source stated no timing; the engine will not guess |
| | `INTRADAY_PRECISION_UNSUPPORTED` | R | daily bars cannot place an intraday event |
| | `NO_SESSION_DATA` | M | no qualifying session (not emitted for a calendar-alignable event since 11.2E) |
| | `NOT_POINT_IN_TIME_ELIGIBLE` | R | alignment withheld in PIT mode |
| `ForwardReturnStatus` | `AVAILABLE` | V | |
| | `NO_ALIGNMENT` | U | depends on alignment |
| | `MISSING_ANCHOR_PRICE` | M | the proven anchor session has no stored price |
| | `INSUFFICIENT_FORWARD_SESSIONS` | M | the exchange calendar cannot prove this many sessions after the anchor (end of its range); no session is named |
| | `MISSING_TARGET_PRICE` | M | the calendar proves the N-th session and no price is stored for it; the proven session is shown and never moved |
| | `INVALID_PRICE` | R | the engine refuses an invalid price |
| | `AMBIGUOUS_PRICE_SOURCE` | A | more than one price source in the window |
| `BenchmarkReturnStatus` | `AVAILABLE` | V | |
| | `NO_BENCHMARK` | **NR** | action: "Select a benchmark" |
| | `NO_STOCK_RETURN` | U | depends on the stock return |
| | `MISSING_ANCHOR_PRICE`, `MISSING_TARGET_PRICE` | M | |
| | `INVALID_PRICE` | R | |
| | `AMBIGUOUS_BENCHMARK_PRICE_SOURCE` | A | |
| `AbnormalReturnStatus` | `AVAILABLE` | V | |
| | `STOCK_RETURN_UNAVAILABLE` | U | |
| | `BENCHMARK_RETURN_UNAVAILABLE` | U, **or NR** | **NR when the same horizon's benchmark status is `NO_BENCHMARK`** |
| `EpsSurpriseStatus` | `AVAILABLE` | V | |
| | `MISSING_REPORTED_EPS`, `MISSING_ESTIMATED_EPS` | NS | the source stated none |
| | `ZERO_ESTIMATE` | U | ratio undefined (absolute may still show) |
| | `NOT_POINT_IN_TIME_ELIGIBLE` | R | |
| `ListingResolutionStatus` | `RESOLVED` | V | |
| | `NO_LISTING_FOR_DATE` | M | |
| | `AMBIGUOUS_LISTING` | A | no remedy control (gap N3) |
| | `CONFLICTING_HISTORY` | A | labelled "Conflicting" |
| `SectionState` (descriptive) | `AVAILABLE` / `PARTIAL` / `UNAVAILABLE` | V / P / U | |
| `PointInTimeState` | `EVIDENCE_ELIGIBLE` | V (PIT hue) | |
| | `REFUSED` | R | callout carries the "does not affect the descriptive study" sentence |
| `PointInTimeEligibility` | `ELIGIBLE` | V (PIT hue) | |
| | `INELIGIBLE_NO_AVAILABILITY_EVIDENCE`, `INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS` | R | short reasons "No availability evidence", "Basis not approved" |
| | `NOT_APPLICABLE` | NA | |
| `AccountingState` | `NOT_REQUESTED` | NR | action: "Link a filing" |
| | `LINK_REFUSED` | R | |
| | `EVALUATED` | V | inner EPS statuses mapped below |
| `FilingLinkStatus` | `NOT_LINKED` | NR | |
| | `FILING_NOT_STORED` | M | |
| | `CIK_MISMATCH_REFUSED` | R | |
| | `CALLER_SUPPLIED_CIK_CONSISTENT` | V | shown with the `PERIOD_UNVERIFIED` caution |
| `PeriodVerification` | `NOT_APPLICABLE` | NA | |
| | `PERIOD_UNVERIFIED` | P-style note | "The filing's period was not verified against the event" |
| `DisclosureEvidenceLevel` | `NONE`, `D0_FILING_EXISTS`, `D1_ITEM_2_02_DECLARED` | category label | never a state; carries "implies no time and no availability" |
| `EpsStatus` (filing, current) | `AVAILABLE` | V | |
| | `NOT_INGESTED`, `MISSING` | M | |
| | `UNSUPPORTED_FORM_PERIOD`, `EXTENSION_ONLY`, `WRONG_UNIT`, `DIMENSIONAL_ONLY`, `COREG_ONLY` | R | the engine refuses these facts |
| | `NULL_VALUE` | NS | "null, not zero" |
| | `AMBIGUOUS` | A | |
| `ComparativeEpsStatus` | `AVAILABLE` | V | |
| | `CURRENT_UNAVAILABLE` | U | |
| | `COMPARATIVE_MISSING` | M | |
| | `COMPARATIVE_PERIOD_MISMATCH`, `COMPARATIVE_DIMENSIONAL_ONLY`, `COMPARATIVE_COREG_ONLY`, `COMPARATIVE_WRONG_UNIT` | R | |
| | `COMPARATIVE_NULL_VALUE` | NS | |
| | `COMPARATIVE_AMBIGUOUS` | A | |
| `EpsChangeStatus` | `AVAILABLE` / `INPUT_UNAVAILABLE` | V / U | |
| `PercentageUnavailableReason` | `ZERO_COMPARATIVE`, `NEGATIVE_COMPARATIVE` | U ("Undefined") | never `0%` |
| `EarningsTiming` | `UNKNOWN` | NS ("Not stated") | the other three are plain labels |
| `RefusalScope` | `EVENT`, `MARKET`, `BENCHMARK`, `FILING_LINK`, `ACCOUNTING`, `POINT_IN_TIME` | group headings | in the ledger |
| `ReproducibilityLevel` | `COMPLETE` / `PARTIAL` | badge | not a domain state; `PARTIAL` is never an error |
| `ReproducibilityGap` | five values | gap sentences | Appendix C |
| Verification status | `MATCH`, `EVIDENCE_INCOMPLETE`, `MANIFEST_CHANGED`, `RESULT_CHANGED` | own treatment | section 9.3 |

An event with no stored value at all (null `observed_at`, null `symbol`, null `report_date`, null `event_timestamp`) is
`NS`, never `M`: the source did not state it, and nothing was lost.

---

## Appendix C. Copy deck (labels and one-line explanations)

Labels are short; each has one sentence the UI can show on focus or in the drawer. The backend's own `reason` strings
are shown verbatim in addition and are never replaced by this copy.

### C.1 Reproducibility gaps

| Gap | Sentence |
|---|---|
| `EVENT_WITHOUT_INGESTION_RUN` | "The earnings observation was stored without a recorded ingestion run." |
| `STOCK_BAR_WITHOUT_INGESTION_RUN` | "At least one stock price bar used here has no recorded ingestion run." |
| `BENCHMARK_BARS_WITHOUT_INGESTION_RUN` | "At least one benchmark level used here has no recorded ingestion run (every level stored before migration 0013 is unstamped)." |
| `FILING_WITHOUT_INGESTION_RUN` | "The linked filing was stored without a recorded ingestion run." |
| `ACCOUNTING_NOT_INGESTED` | "Accounting was evaluated but no FSDS package is stored for the filing, so its absence is not pinned." |

Residual assumptions (always on a saved record): "Stored price bars and filings are treated as immutable by
convention. Listing reference rows are not snapshotted; only the resolved identity is pinned."

### C.2 State labels

| State | Short label | On-focus sentence |
|---|---|---|
| `NOT_STATED` | Not stated | "The source did not supply this." |
| `NOT_REQUESTED` | Not requested | "You did not ask for this. {action}" |
| `NOT_APPLICABLE` | Not applicable | "This does not apply here." |
| `MISSING` | Missing | "Stored data needed for this is absent." |
| `UNAVAILABLE` | Unavailable | "This cannot be derived because an input was unavailable." |
| `REFUSED` | Refused | "The engine declined to compute this by design." |
| `AMBIGUOUS` | Ambiguous | "More than one candidate exists; the engine did not choose." |
| `PARTIAL` | Partial | "Some parts are available and some are not." |
| `ERROR` | Error | "The application could not complete this request." |

### C.3 Fixed sentences

- PIT refusal: "This does not affect the descriptive study above."
- Descriptive frame question: "What happened after this event."
- PIT frame question: "What could have been known and acted on, given the availability evidence."
- Abnormal return note: "Split-adjusted price returns, dividends excluded. A simple market-adjusted figure (stock return minus benchmark return, implicit beta of 1); not a CAPM or factor alpha."
- Comparative EPS: "As presented in this filing: the prior-period figure in this filing's own comparative column."
- Disclosure evidence: "A category of stored metadata. It implies no time and no availability."
- Saved baseline: "Historical stored baseline (immutable)." Rerun: "Current rerun against evidence stored now (not saved)."
- MATCH caveat: "This does not prove that unpinned external source data still exists."
- Unsaved study: "Reproducibility is assessed when a study is saved as a baseline."

---

## Appendix D. URL parameter specification

| Route | Parameter | Values / rule | Invalid handling |
|---|---|---|---|
| `/earnings`, `/research` | `ticker` | 1 to 32 characters, no leading/trailing space | local error, no request |
| | `date_from`, `date_to` | `YYYY-MM-DD`; from not after to | local error |
| | `page` | integer 1 to 10,000 | local error |
| `/filings` | `form` | one of `10-K, 10-K/A, 10-Q, 10-Q/A, 8-K, 8-K/A`, single value | local error |
| | `cik` | exactly 10 digits, single value | local error |
| | `page` | as above | local error |
| `/filings/[accession]` | path | dashed accession `^\d{10}-\d{2}-\d{6}$` | "Malformed accession", no request |
| | `cik` | 10 digits; an empty `cik=` is invalid (not absent) | "Unsupported registrant CIK", controls withheld |
| | `period_class` | `QUARTER` or `ANNUAL` | invalid or missing: no request |
| | `eps_kind` | `DILUTED` or `BASIC_AND_DILUTED` | invalid or missing: no request |
| `/research/events/[eventId]` | path | UUID | not-found page |
| | `benchmark_id` | UUID, single | local error; the id need not be in the list |
| | `horizons` | comma list of positive integers, no spaces, no duplicates, at most 10, each <= 252 | local error |
| | `filing_accession` | dashed accession; **requires both selectors** | local error |
| | `eps_period_class`, `eps_kind` | as for the filing view; **only valid with `filing_accession`** | local error |
| `/research/saved` | `page` | integer | local error |
| `/research/saved/[recordId]` | path | UUID | not-found page |
| | `verify` | `1` only | any other value ignored and not forwarded |

All routes: a repeated parameter is invalid (mirroring the API), unknown parameters are ignored by the UI and never
forwarded to the API, defaults are omitted from generated URLs.

---

## Appendix E. Where the audit findings are resolved

F1 to F14 are listed with evidence in section 2.3 and each names the slice that resolves it. Gaps seen from the UI are
in section 14. Open decisions are in section 16.2.
