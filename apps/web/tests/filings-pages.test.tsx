import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FilingDetailPage from "@/app/filings/[accession]/page";
import FilingsPage from "@/app/filings/page";
import { TRANSITIONS } from "@/types/filings";
import { TRANSITION_LABEL } from "@/lib/filings-view";
import { jsonResponse } from "./fixtures";
import {
  UNAVAILABLE_CHANGE,
  filingsPage,
  makeEps,
  makeFact,
  makeFiling,
} from "./filings-fixtures";

vi.mock("next/navigation", () => ({
  usePathname: () => "/filings",
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

const HEALTH = {
  status: "ok",
  service: "quantforge-api",
  version: "0.1.0",
  environment: "local",
};
const ACC = "0000320193-24-000010";
type Json = Record<string, unknown>;

type Reply = Response | Error;

interface Routes {
  filings?: Reply;
  /** One reply for every EPS request, or a function choosing the reply from the request URL. */
  eps?: Reply | ((url: string) => Reply);
}

function route(routes: Routes) {
  const respond = (value: Reply | undefined): Promise<Response> => {
    if (value === undefined) return Promise.resolve(jsonResponse({}, 500));
    return value instanceof Error
      ? Promise.reject(value)
      : Promise.resolve(value.clone());
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      if (input.includes("/filings") && input.includes("/eps")) {
        return respond(
          typeof routes.eps === "function" ? routes.eps(input) : routes.eps,
        );
      }
      if (input.includes("/api/v1/filings")) return respond(routes.filings);
      return respond(jsonResponse(HEALTH));
    }),
  );
}

const calls = () => vi.mocked(fetch).mock.calls.map((c) => String(c[0]));
const apiCalls = (needle: string) => calls().filter((u) => u.includes(needle));
const listProps = (search: Record<string, string | string[]> = {}) =>
  ({
    searchParams: Promise.resolve(search),
    params: Promise.resolve({}),
  }) as never;
const detailProps = (
  search: Record<string, string | string[]> = {},
  accession = ACC,
) =>
  ({
    searchParams: Promise.resolve(search),
    params: Promise.resolve({ accession }),
  }) as never;
const SELECTED = { period_class: "QUARTER", eps_kind: "DILUTED" };

beforeEach(() => route({}));
afterEach(() => vi.unstubAllGlobals());

describe("Filings list page", () => {
  it("renders rows with exact forms and per-row navigation carrying explicit selectors", async () => {
    route({
      filings: jsonResponse(
        filingsPage([
          makeFiling({ accession: "0000320193-24-000010", form: "10-Q" }),
          makeFiling({ accession: "0000320193-24-000020", form: "10-Q/A" }),
          makeFiling({ accession: "0000320193-23-000106", form: "10-K" }),
          makeFiling({ accession: "0000320193-23-000107", form: "10-K/A" }),
        ]),
      ),
    });
    render(await FilingsPage(listProps()));

    const table = screen.getByRole("table");
    for (const header of [
      "Filing date",
      "Form",
      "CIK",
      "Accession",
      "Report date",
      "FSDS period end",
    ]) {
      expect(
        within(table).getByRole("columnheader", { name: header }),
      ).toBeInTheDocument();
    }
    const hrefs = within(table)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/filings/0000320193-24-000010?cik=0000320193&period_class=QUARTER&eps_kind=DILUTED",
      "/filings/0000320193-24-000020?cik=0000320193&period_class=QUARTER&eps_kind=DILUTED",
      "/filings/0000320193-23-000106?cik=0000320193&period_class=ANNUAL&eps_kind=DILUTED",
      "/filings/0000320193-23-000107?cik=0000320193&period_class=ANNUAL&eps_kind=DILUTED",
    ]);
    expect(within(table).getByText("10-Q/A")).toBeInTheDocument();
    expect(screen.getByText(/4 stored filings/)).toBeInTheDocument();
  });

  it("keeps a joint accession as two rows and does not de-duplicate", async () => {
    route({
      filings: jsonResponse(
        filingsPage([makeFiling({ company_cik: "0000555555" }), makeFiling()]),
      ),
    });
    render(await FilingsPage(listProps()));

    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("0000555555");
    expect(rows[1]).toHaveTextContent("0000320193");
    expect(screen.getByText(/appears as two rows/)).toBeInTheDocument();
  });

  it("shows neutral placeholders for null CIK and dates, never a fake date", async () => {
    route({
      filings: jsonResponse(
        filingsPage([
          makeFiling({
            company_cik: null,
            report_date: null,
            fsds_period_end: null,
          }),
        ]),
      ),
    });
    render(await FilingsPage(listProps()));

    const row = within(screen.getByRole("table")).getAllByRole(
      "row",
    )[1] as HTMLElement;
    expect(within(row).getAllByText("Not stated")).toHaveLength(3);
    expect(row.textContent).not.toMatch(/1970|Invalid Date|NaN|undefined|null/);
  });

  it("shows no ticker, company, exchange, EPS, acceptance or availability columns", async () => {
    route({ filings: jsonResponse(filingsPage([makeFiling()])) });
    render(await FilingsPage(listProps()));

    const headers = within(screen.getByRole("table"))
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual([
      "Filing date",
      "Form",
      "CIK",
      "Accession",
      "Report date",
      "FSDS period end",
    ]);
  });

  it("pages by offset using the API total and keeps the filters in the links", async () => {
    route({
      filings: jsonResponse(filingsPage([makeFiling()], 120, 50)),
    });
    render(
      await FilingsPage(
        listProps({ page: "2", form: "10-Q", cik: "0000320193" }),
      ),
    );

    expect(apiCalls("/api/v1/filings")[0]).toContain("offset=50");
    expect(apiCalls("/api/v1/filings")[0]).toContain("limit=50");
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    expect(within(nav).getByRole("link", { name: /Newer/ })).toHaveAttribute(
      "href",
      "/filings?form=10-Q&cik=0000320193",
    );
    expect(within(nav).getByRole("link", { name: /Older/ })).toHaveAttribute(
      "href",
      "/filings?form=10-Q&cik=0000320193&page=3",
    );
    expect(screen.getByText(/can shift later pages/)).toBeInTheDocument();
  });

  it("sends exact form and ten-digit CIK filters, leading zeros intact", async () => {
    route({ filings: jsonResponse(filingsPage([])) });
    render(await FilingsPage(listProps({ form: "10-Q", cik: "0000320193" })));

    const url = apiCalls("/api/v1/filings")[0];
    expect(url).toContain("form=10-Q");
    expect(url).toContain("cik=0000320193");
    expect(
      screen.getByText("No stored filings match these filters"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/10-Q does not include 10-Q\/A/),
    ).toBeInTheDocument();
  });

  it.each([
    ["an unknown form", { form: "S-1" }],
    ["a lower-case form", { form: "10-q" }],
    ["a short CIK", { cik: "320193" }],
    ["a non-numeric CIK", { cik: "ABCDEFGHIJ" }],
    ["a CIK with surrounding spaces", { cik: " 0000320193" }],
    ["a repeated form", { form: ["10-Q", "S-1"] }],
    ["a repeated CIK", { cik: ["0000320193", "0000555555"] }],
    ["a non-numeric page", { page: "2abc" }],
    ["page zero", { page: "0" }],
    ["a negative page", { page: "-3" }],
    ["a page beyond the offset bound", { page: "10001" }],
  ])("rejects %s locally without calling the API", async (_name, search) => {
    render(await FilingsPage(listProps(search)));

    expect(screen.getByRole("alert")).toHaveTextContent("Invalid filter");
    expect(apiCalls("/api/v1/filings")).toHaveLength(0);
  });

  it("explains an empty index without claiming no SEC filings exist", async () => {
    route({ filings: jsonResponse(filingsPage([])) });
    render(await FilingsPage(listProps()));

    expect(
      screen.getByText("No FSDS-backed filings are currently stored"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/does not mean no filings exist/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("treats a page past the end as such, not as empty data", async () => {
    route({ filings: jsonResponse(filingsPage([], 3, 500)) });
    render(await FilingsPage(listProps({ page: "11" })));

    expect(
      screen.getByText("This page is past the last result"),
    ).toBeInTheDocument();
  });

  it("shows a database-unavailable state for 503, never an empty list", async () => {
    route({ filings: jsonResponse({ detail: "Database unavailable." }, 503) });
    render(await FilingsPage(listProps()));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The database is unavailable",
    );
    expect(screen.queryByText(/No FSDS-backed filings/)).toBeNull();
  });

  it("shows an explicit unsupported-response error for an unknown form value", async () => {
    route({
      filings: jsonResponse(
        filingsPage([makeFiling({ form: "S-1" as never })]),
      ),
    });
    render(await FilingsPage(listProps()));

    expect(screen.getByRole("alert")).toHaveTextContent("unsupported response");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("issues only GET requests to the QuantForge API", async () => {
    route({ filings: jsonResponse(filingsPage([makeFiling()])) });
    render(await FilingsPage(listProps()));

    for (const [url, init] of vi.mocked(fetch).mock.calls) {
      expect(String(url)).toMatch(/^http:\/\/localhost:8000\/api\/v1\//);
      expect((init as RequestInit).method).toBe("GET");
    }
  });
});

const BOUNDARY = /Descriptive accounting data from SEC FSDS\./;

function expectBoundary() {
  const banner = screen.getByRole("note", { name: "Data boundary" });
  expect(banner).toHaveTextContent(
    "Descriptive accounting data from SEC FSDS.",
  );
  expect(banner).toHaveTextContent("No point-in-time availability claim.");
  expect(banner).toHaveTextContent("Not a trading signal.");
}

describe("Filing detail page: selectors", () => {
  it("shows a chooser and makes no EPS request when both selectors are absent", async () => {
    render(await FilingDetailPage(detailProps()));

    expect(screen.getByText("Choose what to show")).toBeInTheDocument();
    expect(screen.getByLabelText("Period class")).toHaveValue("");
    expect(screen.getByLabelText("EPS kind")).toHaveValue("");
    expect(apiCalls("/eps")).toHaveLength(0);
    expectBoundary();
  });

  it("asks only for the missing selector", async () => {
    render(await FilingDetailPage(detailProps({ eps_kind: "DILUTED" })));

    expect(screen.getByRole("status")).toHaveTextContent("period class");
    expect(screen.getByRole("status")).not.toHaveTextContent("EPS kind");
    expect(screen.getByLabelText("EPS kind")).toHaveValue("DILUTED");
    expect(apiCalls("/eps")).toHaveLength(0);
  });

  it.each([
    ["period_class", { period_class: "YTD", eps_kind: "DILUTED" }],
    ["eps_kind", { period_class: "QUARTER", eps_kind: "BASIC" }],
    ["a lower-case value", { period_class: "quarter", eps_kind: "DILUTED" }],
    [
      "a repeated parameter",
      { period_class: ["QUARTER", "ANNUAL"], eps_kind: "DILUTED" },
    ],
  ])(
    "reports an unsupported %s locally, with no API call and no fallback",
    async (_n, search) => {
      render(await FilingDetailPage(detailProps(search)));

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Unsupported parameter",
      );
      expect(apiCalls("/eps")).toHaveLength(0);
      expectBoundary();
    },
  );

  it("rejects a malformed accession before any request", async () => {
    render(await FilingDetailPage(detailProps(SELECTED, "not-an-accession")));

    expect(screen.getByRole("alert")).toHaveTextContent("Malformed accession");
    expect(apiCalls("/eps")).toHaveLength(0);
    expectBoundary();
  });

  it.each([
    [{ period_class: "QUARTER", eps_kind: "DILUTED" }],
    [{ period_class: "ANNUAL", eps_kind: "DILUTED" }],
    [{ period_class: "QUARTER", eps_kind: "BASIC_AND_DILUTED" }],
    [{ period_class: "ANNUAL", eps_kind: "BASIC_AND_DILUTED" }],
  ])("sends exactly the explicit selectors %j", async (search) => {
    route({ eps: jsonResponse(makeEps(search)) });
    render(await FilingDetailPage(detailProps(search)));

    const url = apiCalls("/eps")[0];
    expect(url).toContain(`/api/v1/filings/${ACC}/eps?`);
    expect(url).toContain(`period_class=${search.period_class}`);
    expect(url).toContain(`eps_kind=${search.eps_kind}`);
    expect(screen.getByLabelText("Period class")).toHaveValue(
      search.period_class,
    );
    expect(screen.getByLabelText("EPS kind")).toHaveValue(search.eps_kind);
  });

  it("does not fall back to BASIC_AND_DILUTED when DILUTED is missing", async () => {
    route({
      eps: jsonResponse(
        makeEps({
          current: {
            status: "MISSING",
            reason: "No diluted EPS fact is stored.",
            fact: null,
          },
          comparative: {
            status: "CURRENT_UNAVAILABLE",
            reason: "The current EPS is unavailable.",
            meaning: "comparative EPS as presented in this filing",
            fact: null,
          },
          change: UNAVAILABLE_CHANGE,
        }),
      ),
    });
    render(await FilingDetailPage(detailProps(SELECTED)));

    expect(apiCalls("/eps")).toHaveLength(1);
    expect(apiCalls("/eps")[0]).toContain("eps_kind=DILUTED");
  });
});

describe("Filing detail page: AVAILABLE chain", () => {
  async function renderAvailable(overrides: Json = {}) {
    route({ eps: jsonResponse(makeEps(overrides)) });
    render(await FilingDetailPage(detailProps(SELECTED)));
  }

  it("shows current, comparative and change exactly as the API sent them", async () => {
    await renderAvailable();

    const current = screen.getByRole("region", { name: "Current EPS" });
    expect(within(current).getByText("1.5300")).toBeInTheDocument();
    expect(within(current).getByText("USD")).toBeInTheDocument();
    expect(within(current).getByText("2024-03-31")).toBeInTheDocument();
    expect(within(current).getByText("AVAILABLE")).toBeInTheDocument();

    const comparative = screen.getByRole("region", { name: "Comparative EPS" });
    expect(
      within(comparative).getByText("As presented in this filing"),
    ).toBeInTheDocument();
    expect(within(comparative).getByText("1.0200")).toBeInTheDocument();
    expect(within(comparative).getByText("2023-03-31")).toBeInTheDocument();

    const change = screen.getByRole("region", { name: "EPS change" });
    expect(within(change).getByText("+0.5100")).toBeInTheDocument();
    expect(within(change).getByText("EPS increased")).toBeInTheDocument();
    expect(within(change).getByText("POSITIVE_INCREASED")).toBeInTheDocument();
    expect(within(change).getByText("+50.00%")).toBeInTheDocument();
    expectBoundary();
  });

  it("keeps the exact ratio and fact evidence in the details, verbatim", async () => {
    const ratio = `0.0066${"0".repeat(45)}1`; // 50 fractional digits
    await renderAvailable({
      change: {
        status: "AVAILABLE",
        eps_change: "0.0100",
        transition: "POSITIVE_INCREASED",
        percentage_change: ratio,
        percentage_semantics: "ratio",
        percentage_unavailable_reason: null,
      },
      current: {
        status: "AVAILABLE",
        reason: "r",
        fact: makeFact({
          segments: "StatementClassOfStockAxis=CommonClassAMember;",
          coreg: "",
        }),
      },
    });

    const change = screen.getByRole("region", { name: "EPS change" });
    expect(within(change).getByText("+0.66%")).toBeInTheDocument();
    const details = screen
      .getByText("Evidence and exact values")
      .closest("details") as HTMLElement;
    expect(within(details).getByText(ratio)).toBeInTheDocument();
    expect(ratio.split(".")[1]).toHaveLength(50);
    expect(
      within(details).getAllByText("EarningsPerShareDiluted"),
    ).toHaveLength(2);
    expect(within(details).getAllByText("us-gaap/2024")).toHaveLength(2);
    expect(
      within(details).getByText(
        "StatementClassOfStockAxis=CommonClassAMember;",
      ),
    ).toBeInTheDocument();
    expect(
      within(details).getAllByText("(empty, as stored)").length,
    ).toBeGreaterThan(0);
    expect(details.textContent).not.toMatch(
      /consolidated|accepted at|available at|known as of/i,
    );
  });

  it("exposes the evidence as an accessible disclosure", async () => {
    await renderAvailable();
    const summary = screen.getByText("Evidence and exact values");
    expect(summary.tagName).toBe("SUMMARY");
    expect(summary.closest("details")).not.toHaveAttribute("open");
  });

  it.each([
    ["trailing zeros", "1.5300", "1.5300"],
    ["a negative value", "-0.5000", "-0.5000"],
    ["zero", "0", "0"],
    ["a tiny fixed-point value", "0.0000001", "0.0000001"],
  ])("renders %s verbatim", async (_n, value, shown) => {
    await renderAvailable({
      current: { status: "AVAILABLE", reason: "r", fact: makeFact({ value }) },
    });
    const current = screen.getByRole("region", { name: "Current EPS" });
    expect(within(current).getByText(shown)).toBeInTheDocument();
  });

  it.each([...TRANSITIONS])(
    "labels transition %s without trading language",
    async (transition) => {
      await renderAvailable({
        change: {
          status: "AVAILABLE",
          eps_change: "0.1000",
          transition,
          percentage_change: null,
          percentage_semantics: "ratio",
          percentage_unavailable_reason: "NEGATIVE_COMPARATIVE",
        },
      });
      expect(
        screen.getByText(TRANSITION_LABEL[transition]),
      ).toBeInTheDocument();
      expect(screen.getByText(transition)).toBeInTheDocument();
      const withoutBanner =
        document.body.textContent?.replace(/Not a trading signal\./g, "") ?? "";
      expect(withoutBanner).not.toMatch(
        /bullish|bearish|\bbeat\b|\bmiss\b|\bbuy\b|\bsell\b|signal/i,
      );
    },
  );

  it("renders PROFIT_TO_LOSS with its defined percentage and no verdict", async () => {
    await renderAvailable({
      current: {
        status: "AVAILABLE",
        reason: "r",
        fact: makeFact({ value: "-0.5000" }),
      },
      change: {
        status: "AVAILABLE",
        eps_change: "-1.5000",
        transition: "PROFIT_TO_LOSS",
        percentage_change: "-1.5",
        percentage_semantics: "ratio",
        percentage_unavailable_reason: null,
      },
    });
    const change = screen.getByRole("region", { name: "EPS change" });
    expect(within(change).getByText("Profit to loss")).toBeInTheDocument();
    expect(within(change).getByText("-1.5000")).toBeInTheDocument();
    expect(within(change).getByText("-150.00%")).toBeInTheDocument();
  });

  it.each([
    ["ZERO_COMPARATIVE", "The comparative EPS is zero."],
    ["NEGATIVE_COMPARATIVE", "The comparative EPS is negative."],
  ])(
    "explains an undefined percentage (%s) and never shows 0%%",
    async (reason, detail) => {
      await renderAvailable({
        change: {
          status: "AVAILABLE",
          eps_change: "1.5000",
          transition: "ZERO_TO_PROFIT",
          percentage_change: null,
          percentage_semantics: "ratio",
          percentage_unavailable_reason: reason,
        },
      });
      const change = screen.getByRole("region", { name: "EPS change" });
      expect(
        within(change).getByText(/not defined for this comparative base/),
      ).toBeInTheDocument();
      expect(within(change).getByText(detail)).toBeInTheDocument();
      expect(change.textContent).not.toMatch(/0(\.0+)?%/);
    },
  );
});

describe("Filing detail page: request integrity and presentation", () => {
  it.each([
    ["another accession", { accession: "0000320193-24-000099" }],
    ["another period class", { period_class: "ANNUAL" }],
    ["another EPS kind", { eps_kind: "BASIC_AND_DILUTED" }],
  ])(
    "refuses a response for %s instead of showing it under this request",
    async (_n, override) => {
      route({ eps: jsonResponse(makeEps(override)) });
      render(await FilingDetailPage(detailProps(SELECTED)));

      expect(screen.getByRole("alert")).toHaveTextContent(
        "does not echo the requested selection",
      );
      expect(screen.queryByRole("region", { name: "Current EPS" })).toBeNull();
      expectBoundary();
    },
  );

  it("says a non-zero ratio that rounds to zero is not zero", async () => {
    route({
      eps: jsonResponse(
        makeEps({
          change: {
            status: "AVAILABLE",
            eps_change: "0.0000001",
            transition: "POSITIVE_INCREASED",
            percentage_change: "0.000012",
            percentage_semantics: "ratio",
            percentage_unavailable_reason: null,
          },
        }),
      ),
    });
    render(await FilingDetailPage(detailProps(SELECTED)));

    const change = screen.getByRole("region", { name: "EPS change" });
    expect(
      within(change).getByText(/Not zero: the ratio is too small/),
    ).toBeInTheDocument();
  });

  it("labels each panel by its visible heading", async () => {
    route({ eps: jsonResponse(makeEps()) });
    render(await FilingDetailPage(detailProps(SELECTED)));

    for (const name of ["Current EPS", "Comparative EPS", "EPS change"]) {
      const region = screen.getByRole("region", { name });
      expect(
        within(region).getByRole("heading", { level: 2, name }),
      ).toBeInTheDocument();
    }
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(ACC);
  });

  it("explains that the selections come from the address and are not inferred", async () => {
    render(await FilingDetailPage(detailProps()));

    expect(screen.getByText(/convenience only/)).toBeInTheDocument();
    expect(
      screen.getByText(/never falls back from one EPS kind to another/),
    ).toBeInTheDocument();
  });
});

describe("Filing detail page: refusals and states", () => {
  it("renders NOT_INGESTED honestly, with the whole chain visible", async () => {
    route({
      eps: jsonResponse(
        makeEps({
          form: null,
          company_cik: null,
          current: {
            status: "NOT_INGESTED",
            reason: "Nothing is stored for this accession.",
            fact: null,
          },
          comparative: {
            status: "CURRENT_UNAVAILABLE",
            reason: "The current EPS is unavailable.",
            meaning: "comparative EPS as presented in this filing",
            fact: null,
          },
          change: UNAVAILABLE_CHANGE,
        }),
      ),
    });
    render(await FilingDetailPage(detailProps(SELECTED)));

    const current = screen.getByRole("region", { name: "Current EPS" });
    expect(within(current).getByText("NOT_INGESTED")).toBeInTheDocument();
    expect(
      within(current).getByText("Nothing is stored for this accession."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Comparative EPS" }),
    ).toHaveTextContent("CURRENT_UNAVAILABLE");
    expect(
      screen.getByRole("region", { name: "EPS change" }),
    ).toHaveTextContent("INPUT_UNAVAILABLE");
    expect(screen.queryByRole("alert")).toBeNull();
    expectBoundary();
  });

  it.each([
    "UNSUPPORTED_FORM_PERIOD",
    "MISSING",
    "EXTENSION_ONLY",
    "WRONG_UNIT",
    "DIMENSIONAL_ONLY",
    "COREG_ONLY",
    "AMBIGUOUS",
  ])(
    "shows the exact current refusal %s with its reason and no value",
    async (status) => {
      route({
        eps: jsonResponse(
          makeEps({
            current: { status, reason: `Why ${status}.`, fact: null },
            comparative: {
              status: "CURRENT_UNAVAILABLE",
              reason: "c",
              meaning: "comparative EPS as presented in this filing",
              fact: null,
            },
            change: UNAVAILABLE_CHANGE,
          }),
        ),
      });
      render(await FilingDetailPage(detailProps(SELECTED)));

      const current = screen.getByRole("region", { name: "Current EPS" });
      expect(within(current).getByText(status)).toBeInTheDocument();
      expect(within(current).getByText(`Why ${status}.`)).toBeInTheDocument();
      expect(within(current).queryByText("Value")).toBeNull();
    },
  );

  it("renders a joint accession's AMBIGUOUS result honestly and picks no company", async () => {
    route({
      eps: jsonResponse(
        makeEps({
          company_cik: null,
          current: {
            status: "AMBIGUOUS",
            reason: "The accession is stored under more than one registrant.",
            fact: null,
          },
          comparative: {
            status: "CURRENT_UNAVAILABLE",
            reason: "c",
            meaning: "comparative EPS as presented in this filing",
            fact: null,
          },
          change: UNAVAILABLE_CHANGE,
        }),
      ),
    });
    render(await FilingDetailPage(detailProps(SELECTED)));

    expect(
      screen.getByText(
        "The accession is stored under more than one registrant.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/CIK —/)).toBeInTheDocument();
  });

  it.each([
    "COMPARATIVE_MISSING",
    "COMPARATIVE_PERIOD_MISMATCH",
    "COMPARATIVE_DIMENSIONAL_ONLY",
    "COMPARATIVE_COREG_ONLY",
    "COMPARATIVE_WRONG_UNIT",
    "COMPARATIVE_AMBIGUOUS",
  ])(
    "shows the exact comparative refusal %s and an unavailable change",
    async (status) => {
      route({
        eps: jsonResponse(
          makeEps({
            comparative: {
              status,
              reason: `Why ${status}.`,
              meaning: "comparative EPS as presented in this filing",
              fact: null,
            },
            change: UNAVAILABLE_CHANGE,
          }),
        ),
      });
      render(await FilingDetailPage(detailProps(SELECTED)));

      const comparative = screen.getByRole("region", {
        name: "Comparative EPS",
      });
      expect(within(comparative).getByText(status)).toBeInTheDocument();
      expect(
        within(comparative).getByText(`Why ${status}.`),
      ).toBeInTheDocument();
      const change = screen.getByRole("region", { name: "EPS change" });
      expect(within(change).getByText("INPUT_UNAVAILABLE")).toBeInTheDocument();
      expect(within(change).getByText(/Not computed/)).toBeInTheDocument();
      expect(within(change).queryByText(/%/)).toBeNull();
    },
  );

  it("makes a null comparative value explicit and never renders it as zero", async () => {
    route({
      eps: jsonResponse(
        makeEps({
          comparative: {
            status: "COMPARATIVE_NULL_VALUE",
            reason: "The matching fact states no value.",
            meaning: "comparative EPS as presented in this filing",
            fact: makeFact({ period_end: "2023-03-31", value: null }),
          },
          change: UNAVAILABLE_CHANGE,
        }),
      ),
    });
    render(await FilingDetailPage(detailProps(SELECTED)));

    const comparative = screen.getByRole("region", { name: "Comparative EPS" });
    expect(
      within(comparative).getByText(
        /Matching source evidence exists, but its value is null/,
      ),
    ).toBeInTheDocument();
    expect(within(comparative).getByText(/null, not zero/)).toBeInTheDocument();
    expect(within(comparative).queryByText("0")).toBeNull();
    expect(within(comparative).queryByText("0.0000")).toBeNull();
  });

  it("shows a database-unavailable state for 503 and keeps the boundary", async () => {
    route({ eps: jsonResponse({ detail: "Database unavailable." }, 503) });
    render(await FilingDetailPage(detailProps(SELECTED)));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The database is unavailable",
    );
    expectBoundary();
  });

  it.each([
    [
      "an unknown current status",
      makeEps({ current: { status: "NEW", reason: "r", fact: null } }),
    ],
    [
      "an unknown transition",
      makeEps({
        change: {
          status: "AVAILABLE",
          eps_change: "1",
          transition: "SPACE_PROFIT",
          percentage_change: null,
          percentage_semantics: "ratio",
          percentage_unavailable_reason: "ZERO_COMPARATIVE",
        },
      }),
    ],
    [
      "an unknown percentage reason",
      makeEps({
        change: {
          status: "AVAILABLE",
          eps_change: "1",
          transition: "ZERO_TO_PROFIT",
          percentage_change: null,
          percentage_semantics: "ratio",
          percentage_unavailable_reason: "BECAUSE",
        },
      }),
    ],
  ])(
    "fails loudly on %s instead of falling back to a generic state",
    async (_n, payload) => {
      route({ eps: jsonResponse(payload) });
      render(await FilingDetailPage(detailProps(SELECTED)));

      expect(screen.getByRole("alert")).toHaveTextContent(
        "The API returned an unsupported response",
      );
      expect(screen.queryByRole("region", { name: "Current EPS" })).toBeNull();
      expectBoundary();
    },
  );

  it("keeps the boundary banner on every state and never issues a mutation", async () => {
    route({ eps: jsonResponse(makeEps()) });
    render(await FilingDetailPage(detailProps(SELECTED)));

    expect(screen.getByText(BOUNDARY, { exact: false })).toBeInTheDocument();
    expectBoundary();
    for (const [url, init] of vi.mocked(fetch).mock.calls) {
      expect(String(url)).toMatch(/^http:\/\/localhost:8000\/api\/v1\//);
      expect((init as RequestInit).method).toBe("GET");
    }
    // The way back is the breadcrumb in the header strip (the body no longer repeats it).
    expect(
      within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", {
        name: "Filings",
      }),
    ).toHaveAttribute("href", "/filings");
  });
});

const CIK_A = "0000320193";
const CIK_B = "0000555555";
const QUALIFIED = { cik: CIK_A, ...SELECTED };

const epsFor = (
  cik: string | null,
  value: string,
  prior: string,
  change: string,
) =>
  makeEps({
    company_cik: cik,
    current: { status: "AVAILABLE", reason: "r", fact: makeFact({ value }) },
    comparative: {
      status: "AVAILABLE",
      reason: "r",
      meaning: "comparative EPS as presented in this filing",
      fact: makeFact({ period_end: "2023-03-31", value: prior }),
    },
    change: {
      status: "AVAILABLE",
      eps_change: change,
      transition: "POSITIVE_INCREASED",
      percentage_change: null,
      percentage_semantics: "ratio",
      percentage_unavailable_reason: "NEGATIVE_COMPARATIVE",
    },
  });

describe("Registrant-qualified detail (10.6M)", () => {
  it("sends the CIK from the address, leading zeros intact, in a single request", async () => {
    route({ eps: jsonResponse(epsFor(CIK_A, "1.5300", "1.5200", "0.0100")) });
    render(await FilingDetailPage(detailProps(QUALIFIED)));

    expect(apiCalls("/eps")).toHaveLength(1);
    expect(apiCalls("/eps")[0]).toContain(`cik=${CIK_A}`);
    expect(
      screen.getByRole("heading", { level: 1 }).parentElement,
    ).toHaveTextContent(`CIK ${CIK_A}`);
  });

  it("keeps the CIK in the selector form so changing a selector cannot drop identity", async () => {
    route({ eps: jsonResponse(epsFor(CIK_A, "1.5300", "1.5200", "0.0100")) });
    render(await FilingDetailPage(detailProps(QUALIFIED)));

    const form = screen.getByRole("form", { name: "EPS selection" });
    const hidden = form.querySelector(
      'input[type="hidden"][name="cik"]',
    ) as HTMLInputElement;
    expect(hidden.value).toBe(CIK_A);
    expect(form).toHaveAttribute("action", `/filings/${ACC}`);
    expect(form.getAttribute("method")).toBe("get");
  });

  it("adds no CIK field and no CIK parameter for an accession-only address", async () => {
    route({ eps: jsonResponse(makeEps()) });
    render(await FilingDetailPage(detailProps(SELECTED)));

    expect(apiCalls("/eps")[0]).not.toContain("cik=");
    expect(
      screen
        .getByRole("form", { name: "EPS selection" })
        .querySelector('input[name="cik"]'),
    ).toBeNull();
  });

  it("supports a direct qualified URL: the chooser appears, no request is made, the CIK is kept", async () => {
    render(await FilingDetailPage(detailProps({ cik: CIK_A })));

    expect(screen.getByText("Choose what to show")).toBeInTheDocument();
    expect(apiCalls("/eps")).toHaveLength(0);
    const hidden = screen
      .getByRole("form", { name: "EPS selection" })
      .querySelector('input[name="cik"]') as HTMLInputElement;
    expect(hidden.value).toBe(CIK_A);
    expect(
      screen.getByRole("heading", { level: 1 }).parentElement,
    ).toHaveTextContent(`CIK ${CIK_A}`);
    expectBoundary();
  });

  it.each([
    ["a short CIK", "320193"],
    ["a CIK with spaces", " 0000320193"],
    ["a non-numeric CIK", "ABCDEFGHIJ"],
    ["an over-long CIK", "00003201930"],
    ["an empty CIK", ""],
  ])(
    "rejects %s locally: no request, no fallback, and no control that would drop it",
    async (_n, cik) => {
      render(await FilingDetailPage(detailProps({ cik, ...SELECTED })));

      expect(screen.getByRole("alert")).toHaveTextContent(
        "Unsupported registrant CIK",
      );
      expect(apiCalls("/eps")).toHaveLength(0);
      // re-submitting the selectors would silently request the accession alone
      expect(screen.queryByRole("form", { name: "EPS selection" })).toBeNull();
      expect(
        screen.getByRole("link", { name: "filings list" }),
      ).toHaveAttribute("href", "/filings");
      expectBoundary();
    },
  );

  it("rejects a repeated cik parameter locally", async () => {
    render(
      await FilingDetailPage(detailProps({ cik: [CIK_A, CIK_B], ...SELECTED })),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unsupported registrant CIK",
    );
    expect(apiCalls("/eps")).toHaveLength(0);
    expect(screen.queryByRole("form", { name: "EPS selection" })).toBeNull();
  });

  it("resolves the two registrants of a joint accession independently, never mixing them", async () => {
    route({
      eps: (url) =>
        url.includes(`cik=${CIK_A}`)
          ? jsonResponse(epsFor(CIK_A, "1.5300", "1.5200", "0.0100"))
          : url.includes(`cik=${CIK_B}`)
            ? jsonResponse(epsFor(CIK_B, "2.0000", "1.0000", "1.0000"))
            : jsonResponse(
                makeEps({
                  company_cik: null,
                  current: {
                    status: "AMBIGUOUS",
                    reason: "stored under more than one registrant",
                    fact: null,
                  },
                  comparative: {
                    status: "CURRENT_UNAVAILABLE",
                    reason: "c",
                    meaning: "comparative EPS as presented in this filing",
                    fact: null,
                  },
                  change: UNAVAILABLE_CHANGE,
                }),
              ),
    });

    render(await FilingDetailPage(detailProps({ cik: CIK_A, ...SELECTED })));
    const a = screen.getByRole("region", { name: "Current EPS" });
    expect(within(a).getByText("1.5300")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "EPS change" }),
    ).toHaveTextContent("+0.0100");
    cleanup();

    render(await FilingDetailPage(detailProps({ cik: CIK_B, ...SELECTED })));
    const b = screen.getByRole("region", { name: "Current EPS" });
    expect(within(b).getByText("2.0000")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "EPS change" }),
    ).toHaveTextContent("+1.0000");
    expect(document.body.textContent).not.toContain("1.5300");
    cleanup();

    // and the unqualified address still reports the ambiguity honestly
    render(await FilingDetailPage(detailProps(SELECTED)));
    expect(screen.getByText("AMBIGUOUS")).toBeInTheDocument();
    expect(
      apiCalls("/eps").map((u) => /cik=(\d+)/.exec(u)?.[1] ?? null),
    ).toEqual([CIK_A, CIK_B, null]);
  });

  it.each([
    ["another registrant", epsFor(CIK_B, "2.0000", "1.0000", "1.0000")],
    ["no registrant", epsFor(null, "1.5300", "1.5200", "0.0100")],
  ])(
    "refuses a qualified response naming %s as a contract error",
    async (_n, payload) => {
      route({ eps: jsonResponse(payload) });
      render(await FilingDetailPage(detailProps(QUALIFIED)));

      expect(screen.getByRole("alert")).toHaveTextContent(
        "The API returned an unsupported response",
      );
      expect(screen.getByRole("alert")).toHaveTextContent(
        "requested registrant",
      );
      expect(screen.queryByRole("region", { name: "Current EPS" })).toBeNull();
      expectBoundary();
    },
  );

  it("never retries a qualified request without the CIK (mismatch, refusal, 503 or 422)", async () => {
    const cases: Response[] = [
      jsonResponse(epsFor(CIK_B, "2.0000", "1.0000", "1.0000")), // wrong registrant echoed
      jsonResponse({ detail: "Database unavailable." }, 503),
      jsonResponse({ detail: "bad" }, 422),
      jsonResponse(
        makeEps({
          company_cik: CIK_A,
          form: null,
          current: {
            status: "NOT_INGESTED",
            reason: "no stored SecFiling for that CIK",
            fact: null,
          },
          comparative: {
            status: "CURRENT_UNAVAILABLE",
            reason: "c",
            meaning: "comparative EPS as presented in this filing",
            fact: null,
          },
          change: UNAVAILABLE_CHANGE,
        }),
      ),
    ];
    for (const reply of cases) {
      route({ eps: reply });
      render(await FilingDetailPage(detailProps(QUALIFIED)));
      const eps = apiCalls("/eps");
      expect(eps).toHaveLength(1);
      expect(eps[0]).toContain(`cik=${CIK_A}`);
      cleanup();
    }
  });

  it("shows a qualified NOT_INGESTED honestly with the requested registrant, not another's data", async () => {
    route({
      eps: jsonResponse(
        makeEps({
          company_cik: CIK_B,
          form: null,
          current: {
            status: "NOT_INGESTED",
            reason: `no stored SecFiling for ${ACC} and CIK ${CIK_B}`,
            fact: null,
          },
          comparative: {
            status: "CURRENT_UNAVAILABLE",
            reason: "c",
            meaning: "comparative EPS as presented in this filing",
            fact: null,
          },
          change: UNAVAILABLE_CHANGE,
        }),
      ),
    });
    render(await FilingDetailPage(detailProps({ cik: CIK_B, ...SELECTED })));

    expect(
      screen.getByRole("region", { name: "Current EPS" }),
    ).toHaveTextContent("NOT_INGESTED");
    expect(screen.queryByRole("alert")).toBeNull();
    const header = screen.getByRole("heading", { level: 1 }).parentElement;
    expect(header).toHaveTextContent(`Requested CIK ${CIK_B}`);
    expect(header).toHaveTextContent("request echo, not a stored registrant");
  });

  it("labels a resolved filing's CIK as the registrant, not as a request echo", async () => {
    route({ eps: jsonResponse(epsFor(CIK_A, "1.5300", "1.5200", "0.0100")) });
    render(await FilingDetailPage(detailProps(QUALIFIED)));

    const header = screen.getByRole("heading", { level: 1 }).parentElement;
    expect(header).toHaveTextContent(`10-Q · CIK ${CIK_A}`);
    expect(header).not.toHaveTextContent("request echo");
  });
});

describe("Filings list: joint accessions link to their own registrant (10.6M)", () => {
  it("gives the two rows of a joint accession different qualified URLs, and a CIK-less row none", async () => {
    route({
      filings: jsonResponse(
        filingsPage([
          makeFiling({ company_cik: CIK_B }),
          makeFiling({ company_cik: CIK_A }),
          makeFiling({ company_cik: null }),
        ]),
      ),
    });
    render(await FilingsPage(listProps()));

    const hrefs = within(screen.getByRole("table"))
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      `/filings/${ACC}?cik=${CIK_B}&period_class=QUARTER&eps_kind=DILUTED`,
      `/filings/${ACC}?cik=${CIK_A}&period_class=QUARTER&eps_kind=DILUTED`,
      `/filings/${ACC}?period_class=QUARTER&eps_kind=DILUTED`,
    ]);
    expect(new Set(hrefs).size).toBe(3);
  });
});
