import Link from "next/link";

import { FilingsTable } from "@/components/filings/filings-table";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { StatePanel } from "@/components/ui/state-panel";
import { PAGE_SIZE } from "@/lib/api/earnings";
import { getFilings, type FilingsQuery } from "@/lib/api/filings";
import { getBackendStatus } from "@/lib/api/health";
import { isCik, isFilingForm } from "@/lib/filings-view";
import { FILING_FORMS } from "@/types/filings";

export const metadata = { title: "Filings · QuantForge" };

/** Keeps (page-1) * PAGE_SIZE inside the API's offset bound. */
const MAX_PAGE = 10_000;

type SearchParams = Record<string, string | string[] | undefined>;

/** A single, untouched value: absent is "", a repeated parameter is flagged, nothing is trimmed. */
function read(value: string | string[] | undefined): {
  text: string;
  repeated: boolean;
} {
  if (Array.isArray(value))
    return { text: value[0] ?? "", repeated: value.length > 1 };
  return { text: value ?? "", repeated: false };
}

const PAGE_NUMBER = /^[1-9]\d{0,4}$/;

function parseQuery(params: SearchParams): {
  query: FilingsQuery;
  page: number;
  invalid: string | null;
} {
  const form = read(params.form);
  const cik = read(params.cik);
  const pageParam = read(params.page);

  let invalid: string | null = null;
  if (form.repeated || (form.text && !isFilingForm(form.text))) {
    invalid = `Unsupported form “${form.text}”. Use one of ${FILING_FORMS.join(", ")} (exact match, one value).`;
  } else if (cik.repeated || (cik.text && !isCik(cik.text))) {
    invalid =
      "CIK must be exactly ten digits, including leading zeros, as a single value.";
  } else if (
    pageParam.repeated ||
    (pageParam.text &&
      (!PAGE_NUMBER.test(pageParam.text) || Number(pageParam.text) > MAX_PAGE))
  ) {
    invalid = `Page must be a whole number from 1 to ${MAX_PAGE}.`;
  }
  const page = invalid || !pageParam.text ? 1 : Number(pageParam.text);

  return {
    query: {
      form:
        !invalid && form.text && isFilingForm(form.text)
          ? form.text
          : undefined,
      cik: !invalid && cik.text ? cik.text : undefined,
      offset: (page - 1) * PAGE_SIZE,
    },
    page,
    invalid,
  };
}

function pageHref(query: FilingsQuery, page: number): string {
  const params = new URLSearchParams();
  if (query.form) params.set("form", query.form);
  if (query.cik) params.set("cik", query.cik);
  if (page > 1) params.set("page", String(page));
  const qs = params.toString();
  return qs ? `/filings?${qs}` : "/filings";
}

const INPUT =
  "rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-muted";

export default async function FilingsPage({
  searchParams,
}: PageProps<"/filings">) {
  const raw = await searchParams;
  const { query, page, invalid } = parseQuery(raw);
  const [status, result] = await Promise.all([
    getBackendStatus(),
    invalid ? Promise.resolve(null) : getFilings(query),
  ]);
  const filtered = Boolean(query.form || query.cik);
  // Keep what the user typed in the form even when it was rejected locally.
  const shownForm = read(raw.form).text;
  const shownCik = read(raw.cik).text;

  return (
    <AppShell status={status}>
      <div className="mx-auto flex max-w-[110rem] flex-col gap-5">
        <PageHeader
          eyebrow="Research"
          title="Filings"
          description="Stored filings that have SEC FSDS financial-fact data, newest filing date first. This is not every SEC filing and says nothing about when a filing was available or tradable. Filing and report dates are source metadata."
        />

        <form
          method="get"
          action="/filings"
          className="flex flex-wrap items-end gap-3"
        >
          <div className="flex flex-col gap-1">
            <label htmlFor="form" className="text-xs text-text-muted">
              Form (exact)
            </label>
            <select
              id="form"
              name="form"
              defaultValue={shownForm}
              className={INPUT}
            >
              <option value="">Any form</option>
              {FILING_FORMS.map((form) => (
                <option key={form} value={form}>
                  {form}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="cik" className="text-xs text-text-muted">
              CIK (ten digits)
            </label>
            <input
              id="cik"
              name="cik"
              defaultValue={shownCik}
              maxLength={10}
              inputMode="numeric"
              pattern="[0-9]{10}"
              title="Exactly ten digits, including leading zeros"
              className={`${INPUT} w-32 font-mono`}
            />
          </div>
          <button
            type="submit"
            className="rounded-md border border-border-strong bg-surface-3 px-3 py-1.5 text-sm text-text-primary hover:border-accent"
          >
            Apply
          </button>
          {shownForm || shownCik ? (
            <Link
              href="/filings"
              className="py-1.5 text-sm text-accent hover:underline"
            >
              Clear
            </Link>
          ) : null}
        </form>
        <p className="-mt-3 text-xs text-text-muted">
          Filters match exactly: 10-Q does not include 10-Q/A.
        </p>

        {invalid ? (
          <StatePanel kind="error" title="Invalid filter">
            {invalid}
          </StatePanel>
        ) : result === null ? null : result.state === "unavailable" ? (
          <StatePanel kind="error" title="The database is unavailable">
            The QuantForge API could not read the filings index. This is a
            service problem, not an empty index. Try again shortly.
          </StatePanel>
        ) : result.state === "contract" ? (
          <StatePanel
            kind="error"
            title="The API returned an unsupported response"
          >
            {result.detail}
          </StatePanel>
        ) : result.state === "rejected" ? (
          <StatePanel kind="error" title="The API rejected these filters">
            {result.detail}
          </StatePanel>
        ) : result.state === "error" ? (
          <StatePanel kind="error" title="Filings are unavailable">
            The QuantForge API did not return the filings index. {result.detail}
          </StatePanel>
        ) : result.data.total === 0 ? (
          filtered ? (
            <StatePanel
              kind="empty"
              title="No stored filings match these filters"
            >
              No FSDS-backed filing matches. Clear the filters to see every
              stored filing.
            </StatePanel>
          ) : (
            <StatePanel
              kind="empty"
              title="No FSDS-backed filings are currently stored"
            >
              Filings appear here once SEC FSDS financial facts have been
              ingested for them. This does not mean no filings exist.
            </StatePanel>
          )
        ) : result.data.items.length === 0 ? (
          <StatePanel kind="empty" title="This page is past the last result">
            <Link
              href={pageHref(query, 1)}
              className="text-accent hover:underline"
            >
              Back to the first page
            </Link>
          </StatePanel>
        ) : (
          <>
            <p className="text-xs text-text-muted">
              {result.data.total} stored filing
              {result.data.total === 1 ? "" : "s"} · rows{" "}
              {result.data.offset + 1}–
              {result.data.offset + result.data.items.length}. A joint accession
              stored under two registrants appears as two rows, told apart by
              CIK. Paging is by offset: filings ingested while you browse can
              shift later pages. &ldquo;—&rdquo; means the API gave no value;
              the FSDS period end is given only when all of a filing&rsquo;s
              FSDS submissions state the same period.
            </p>
            <FilingsTable
              filings={result.data.items}
              offset={result.data.offset}
            />
            <nav
              aria-label="Pagination"
              className="flex items-center gap-4 text-sm"
            >
              {page > 1 ? (
                <Link
                  href={pageHref(query, page - 1)}
                  className="text-accent hover:underline"
                >
                  ← Newer
                </Link>
              ) : null}
              {result.data.offset + result.data.items.length <
              result.data.total ? (
                <Link
                  href={pageHref(query, page + 1)}
                  className="text-accent hover:underline"
                >
                  Older →
                </Link>
              ) : null}
            </nav>
          </>
        )}
      </div>
    </AppShell>
  );
}
