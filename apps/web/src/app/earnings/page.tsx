import Link from "next/link";

import { EarningsTable } from "@/components/earnings/earnings-table";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { StatePanel } from "@/components/ui/state-panel";
import { PAGE_SIZE, getEarningsPage, type EarningsQuery } from "@/lib/api/earnings";
import { getBackendStatus } from "@/lib/api/health";

export const metadata = { title: "Earnings · QuantForge" };

/** Keeps (page-1) * PAGE_SIZE inside the API's offset bound. */
const MAX_PAGE = 10_000;

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

type SearchParams = Record<string, string | string[] | undefined>;

function one(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value)?.trim() ?? "";
}

function parseQuery(params: SearchParams): {
  query: EarningsQuery;
  page: number;
  invalid: string | null;
} {
  const ticker = one(params.ticker).slice(0, 32);
  const dateFrom = one(params.date_from);
  const dateTo = one(params.date_to);
  const pageNumber = Number.parseInt(one(params.page), 10);
  const page = Number.isInteger(pageNumber) && pageNumber > 0 ? Math.min(pageNumber, MAX_PAGE) : 1;

  let invalid: string | null = null;
  if (dateFrom && !ISO_DATE.test(dateFrom)) invalid = "From date must be YYYY-MM-DD.";
  else if (dateTo && !ISO_DATE.test(dateTo)) invalid = "To date must be YYYY-MM-DD.";
  else if (dateFrom && dateTo && dateFrom > dateTo) invalid = "From date is after the To date.";

  return {
    query: {
      ticker: ticker || undefined,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      offset: (page - 1) * PAGE_SIZE,
    },
    page,
    invalid,
  };
}

function pageHref(query: EarningsQuery, page: number): string {
  const params = new URLSearchParams();
  if (query.ticker) params.set("ticker", query.ticker);
  if (query.dateFrom) params.set("date_from", query.dateFrom);
  if (query.dateTo) params.set("date_to", query.dateTo);
  if (page > 1) params.set("page", String(page));
  const qs = params.toString();
  return qs ? `/earnings?${qs}` : "/earnings";
}

const INPUT =
  "rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-muted";

export default async function EarningsPage({ searchParams }: PageProps<"/earnings">) {
  const { query, page, invalid } = parseQuery(await searchParams);
  const [status, result] = await Promise.all([
    getBackendStatus(),
    invalid ? Promise.resolve(null) : getEarningsPage(query),
  ]);
  const filtered = Boolean(query.ticker || query.dateFrom || query.dateTo);

  return (
    <AppShell status={status}>
      <div className="mx-auto flex max-w-[110rem] flex-col gap-5">
        <PageHeader
          eyebrow="Research"
          title="Earnings explorer"
          description="Stored earnings observations with their availability evidence. Point-in-time status is the backend's classification; this page does not recompute it."
        />

        <form method="get" action="/earnings" className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="ticker" className="text-xs text-text-muted">
              Ticker (valid on the event date)
            </label>
            <input
              id="ticker"
              name="ticker"
              defaultValue={query.ticker ?? ""}
              maxLength={32}
              className={`${INPUT} w-32 font-mono uppercase`}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="date_from" className="text-xs text-text-muted">
              From
            </label>
            <input
              id="date_from"
              name="date_from"
              type="date"
              defaultValue={query.dateFrom ?? ""}
              className={INPUT}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="date_to" className="text-xs text-text-muted">
              To
            </label>
            <input
              id="date_to"
              name="date_to"
              type="date"
              defaultValue={query.dateTo ?? ""}
              className={INPUT}
            />
          </div>
          <button
            type="submit"
            className="rounded-md border border-border-strong bg-surface-3 px-3 py-1.5 text-sm text-text-primary hover:border-accent"
          >
            Apply
          </button>
          {filtered ? (
            <Link href="/earnings" className="py-1.5 text-sm text-accent hover:underline">
              Clear
            </Link>
          ) : null}
        </form>

        {invalid ? (
          <StatePanel kind="error" title="Invalid filter">
            {invalid}
          </StatePanel>
        ) : result === null ? null : result.state === "error" ? (
          <StatePanel kind="error" title="Earnings data is unavailable">
            The QuantForge API did not return earnings events. {result.detail}
          </StatePanel>
        ) : result.state === "not_found" ? (
          <StatePanel kind="error" title="Earnings endpoint not found">
            The API does not expose the earnings-events endpoint. Is the backend up to date?
          </StatePanel>
        ) : result.data.total === 0 ? (
          filtered ? (
            <StatePanel kind="empty" title="No events match these filters">
              Nothing stored matches. Clear the filters to see all events.
            </StatePanel>
          ) : (
            <StatePanel kind="empty" title="No earnings events are available yet">
              No earnings have been ingested. Events appear here after the ingestion pipeline
              has loaded them; nothing is shown in the meantime.
            </StatePanel>
          )
        ) : result.data.items.length === 0 ? (
          <StatePanel kind="empty" title="This page is past the last result">
            <Link href={pageHref(query, 1)} className="text-accent hover:underline">
              Back to the first page
            </Link>
          </StatePanel>
        ) : (
          <>
            <p className="text-xs text-text-muted">
              {result.data.total} stored observation{result.data.total === 1 ? "" : "s"} · rows{" "}
              {result.data.offset + 1}–{result.data.offset + result.data.items.length}, newest
              first. Each row is one stored observation: a revision or a second source for the
              same fiscal period appears as its own row. Many observations have no availability
              evidence because providers do not supply historical publication times; these are
              labelled, not hidden.
            </p>
            <EarningsTable events={result.data.items} />
            <nav aria-label="Pagination" className="flex items-center gap-4 text-sm">
              {page > 1 ? (
                <Link href={pageHref(query, page - 1)} className="text-accent hover:underline">
                  ← Newer
                </Link>
              ) : null}
              {result.data.offset + result.data.items.length < result.data.total ? (
                <Link href={pageHref(query, page + 1)} className="text-accent hover:underline">
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
