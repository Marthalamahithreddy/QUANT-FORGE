/**
 * The descriptive accounting vertical, frontend half.
 *
 * `tests/contract/vertical.json` is the JSON the real backend returned for a coherent seeded
 * scenario (pinned by the backend integration test `test_vertical_contract.py`). Here the very same
 * file is parsed by the strict parsers and rendered by the real pages: the index row's link, the
 * request it produces, the response echo and the displayed values.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { decodeFilingEps, decodeFilingIndexPage } from "@/lib/api/decoders/filings";
import FilingDetailPage from "@/app/filings/[accession]/page";
import FilingsPage from "@/app/filings/page";
import { FilingsTable } from "@/components/filings/filings-table";
import {
  CHANGE_STATUS_LABEL,
  COMPARATIVE_STATUS_LABEL,
  EPS_STATUS_LABEL,
  PERCENTAGE_REASON_DETAIL,
  TRANSITION_LABEL,
} from "@/lib/filings-view";
import {
  CHANGE_STATUSES,
  COMPARATIVE_STATUSES,
  EPS_KINDS,
  EPS_STATUSES,
  FILING_FORMS,
  PERCENTAGE_REASONS,
  PERIOD_CLASSES,
  TRANSITIONS,
} from "@/lib/api/schema";
import { jsonResponse } from "./fixtures";
import { renderServer } from "./render-server";
import { filingsPage, makeFiling } from "./filings-fixtures";

vi.mock("next/navigation", () => ({
  usePathname: () => "/filings",
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

interface Request {
  accession: string;
  cik: string | null;
  period_class: string;
  eps_kind: string;
}
interface Golden {
  enums: Record<string, string[]>;
  index: { items: Record<string, string | null>[]; total: number };
  eps: { request: Request; response: Record<string, unknown> }[];
}

const parseFilingEps = (value: unknown) => decodeFilingEps(value, "$");
const parseFilingIndexPage = (value: unknown) => decodeFilingIndexPage(value, "$");

const GOLDEN: Golden = JSON.parse(
  readFileSync(join(process.cwd(), "tests/contract/vertical.json"), "utf8"),
);
const HEALTH = {
  status: "ok",
  service: "quantforge-api",
  version: "0.1.0",
  environment: "local",
};

const ACC = "0000320193-24-000010";
const CIK_A = "0000320193";
const CIK_B = "0000555555";

function goldenFor(req: Request): Record<string, unknown> | undefined {
  return GOLDEN.eps.find(
    (e) =>
      e.request.accession === req.accession &&
      e.request.cik === req.cik &&
      e.request.period_class === req.period_class &&
      e.request.eps_kind === req.eps_kind,
  )?.response;
}

/** Serves the pinned backend JSON: an EPS request with no pinned answer fails the test. */
function serveGolden() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = new URL(input);
      if (url.pathname.endsWith("/eps")) {
        const accession = decodeURIComponent(url.pathname.split("/")[4] ?? "");
        const response = goldenFor({
          accession,
          cik: url.searchParams.get("cik"),
          period_class: url.searchParams.get("period_class") ?? "",
          eps_kind: url.searchParams.get("eps_kind") ?? "",
        });
        if (response === undefined) {
          throw new Error(`no pinned backend answer for ${input}`);
        }
        return Promise.resolve(jsonResponse(response));
      }
      if (url.pathname === "/api/v1/filings") {
        return Promise.resolve(jsonResponse(GOLDEN.index));
      }
      return Promise.resolve(jsonResponse(HEALTH));
    }),
  );
}

const epsCalls = () =>
  vi
    .mocked(fetch)
    .mock.calls.map((c) => String(c[0]))
    .filter((u) => u.includes("/eps"));

async function openDetail(href: string) {
  const url = new URL(href, "http://localhost");
  const search: Record<string, string> = {};
  url.searchParams.forEach((v, k) => {
    search[k] = v;
  });
  const accession = decodeURIComponent(url.pathname.split("/")[2] ?? "");
  render(
    await FilingDetailPage({
      params: Promise.resolve({ accession }),
      searchParams: Promise.resolve(search),
    } as never),
  );
}

const section = (name: string) => screen.getByRole("region", { name });
const heading = () => screen.getByRole("heading", { level: 1 }).parentElement;

beforeEach(() => serveGolden());
afterEach(() => vi.unstubAllGlobals());

describe("status contract: backend enums = frontend parser = display maps", () => {
  const pairs: [string, readonly string[]][] = [
    ["EpsStatus", EPS_STATUSES],
    ["ComparativeEpsStatus", COMPARATIVE_STATUSES],
    ["EpsChangeStatus", CHANGE_STATUSES],
    ["EpsTransition", TRANSITIONS],
    ["PercentageUnavailableReason", PERCENTAGE_REASONS],
    ["EpsPeriodClass", PERIOD_CLASSES],
    ["EpsKind", EPS_KINDS],
    ["SecFormType", FILING_FORMS],
  ];
  it.each(pairs)("%s is exactly the backend OpenAPI enum", (name, frontend) => {
    expect([...frontend].sort()).toEqual(GOLDEN.enums[name]);
  });

  it("has a display label for every backend value and none for an invented one", () => {
    const maps: [string, Record<string, string>][] = [
      ["EpsStatus", EPS_STATUS_LABEL],
      ["ComparativeEpsStatus", COMPARATIVE_STATUS_LABEL],
      ["EpsChangeStatus", CHANGE_STATUS_LABEL],
      ["EpsTransition", TRANSITION_LABEL],
      ["PercentageUnavailableReason", PERCENTAGE_REASON_DETAIL],
    ];
    for (const [name, map] of maps) {
      expect(Object.keys(map).sort()).toEqual(GOLDEN.enums[name]);
    }
  });
});

describe("the pinned backend JSON passes the strict parsers", () => {
  it("parses the index and every EPS response, financial values staying strings", () => {
    const page = parseFilingIndexPage(GOLDEN.index);
    expect(page.items).toHaveLength(page.total);
    for (const entry of GOLDEN.eps) {
      const eps = parseFilingEps(entry.response);
      for (const v of [
        eps.current.fact?.value,
        eps.comparative.fact?.value,
        eps.change.eps_change,
        eps.change.percentage_change,
      ]) {
        expect(v === null || v === undefined || typeof v === "string").toBe(
          true,
        );
        if (typeof v === "string") expect(v).not.toMatch(/[eE]/);
      }
    }
  });
});

describe("index row -> link -> request -> response -> rendered detail", () => {
  it("keeps accession and CIK through every hop, for every index row", async () => {
    await renderServer(await FilingsPage({ searchParams: Promise.resolve({}) } as never));
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    const links = within(screen.getByRole("table")).getAllByRole("link");
    expect(links).toHaveLength(GOLDEN.index.items.length);
    expect(rows).toHaveLength(GOLDEN.index.total);
    cleanup();

    for (const [i, item] of GOLDEN.index.items.entries()) {
      const href = links[i]?.getAttribute("href") as string;
      const url = new URL(href, "http://localhost");
      expect(decodeURIComponent(url.pathname)).toBe(
        `/filings/${item.accession}`,
      );
      expect(url.searchParams.get("cik")).toBe(item.company_cik);
      expect(url.searchParams.get("eps_kind")).toBe("DILUTED");
      vi.mocked(fetch).mockClear();
      await openDetail(href);
      expect(epsCalls()).toHaveLength(1);
      const sent = new URL(epsCalls()[0] as string);
      expect(sent.searchParams.get("cik")).toBe(item.company_cik);
      expect(decodeURIComponent(sent.pathname)).toContain(
        item.accession as string,
      );
      expect(screen.queryByRole("alert")).toBeNull();
      expect(section("Current EPS")).toBeInTheDocument();
      if (item.company_cik !== null) {
        expect(heading()).toHaveTextContent(
          `${item.form} · CIK ${item.company_cik}`,
        );
      }
      cleanup();
    }
  });

  it("gives a joint accession two rows with different links and independent values", async () => {
    await renderServer(await FilingsPage({ searchParams: Promise.resolve({}) } as never));
    const joint = GOLDEN.index.items
      .map((item, i) => [item, i] as const)
      .filter(([item]) => item.accession === ACC);
    expect(joint.map(([item]) => item.company_cik).sort()).toEqual([
      CIK_A,
      CIK_B,
    ]);
    const links = within(screen.getByRole("table")).getAllByRole("link");
    const hrefs = joint.map(
      ([, i]) => links[i]?.getAttribute("href") as string,
    );
    expect(new Set(hrefs).size).toBe(2);
    cleanup();

    const values: Record<string, string> = {};
    for (const href of hrefs) {
      await openDetail(href);
      const cik = new URL(href, "http://localhost").searchParams.get(
        "cik",
      ) as string;
      values[cik] =
        within(section("Current EPS")).getByText(/^\d+\.\d{4}$/).textContent ??
        "";
      cleanup();
    }
    expect(values).toEqual({ [CIK_A]: "1.5300", [CIK_B]: "4.0000" });
  });

  it("keeps the null-CIK row visible and accession-only, never inventing identity", async () => {
    await renderServer(await FilingsPage({ searchParams: Promise.resolve({}) } as never));
    const index = GOLDEN.index.items.findIndex((i) => i.company_cik === null);
    expect(index).toBeGreaterThanOrEqual(0);
    const href = within(screen.getByRole("table"))
      .getAllByRole("link")
      [index]?.getAttribute("href") as string;
    expect(href).not.toContain("cik=");
    cleanup();

    await openDetail(href);
    expect(epsCalls()[0]).not.toContain("cik=");
    expect(
      within(section("Current EPS")).getByText("2.0000"),
    ).toBeInTheDocument();
    expect(heading()).toHaveTextContent("CIK —");
  });
});

describe("rendered semantics of the real backend answers", () => {
  const open = (
    accession: string,
    cik: string | null,
    period = "QUARTER",
    kind = "DILUTED",
  ) =>
    openDetail(
      `/filings/${accession}?${cik ? `cik=${cik}&` : ""}period_class=${period}&eps_kind=${kind}`,
    );

  it("happy path: current, same-accession comparative and the domain's change", async () => {
    await open(ACC, CIK_A);
    expect(
      within(section("Current EPS")).getByText("1.5300"),
    ).toBeInTheDocument();
    expect(
      within(section("Comparative EPS")).getByText("1.5200"),
    ).toBeInTheDocument();
    expect(
      within(section("EPS change")).getByText("+0.0100"),
    ).toBeInTheDocument();
    expect(
      within(section("EPS change")).getByText("EPS increased"),
    ).toBeInTheDocument();
    expect(
      within(section("EPS change")).getByText("+0.66%"),
    ).toBeInTheDocument();
  });

  it("joint B shows the 50-digit repeating ratio exactly in the evidence and 33.33% on top", async () => {
    await open(ACC, CIK_B);
    const exact = "0." + "3".repeat(50);
    expect(
      within(section("EPS change")).getByText("+33.33%"),
    ).toBeInTheDocument();
    const details = screen
      .getByText("Evidence and exact values")
      .closest("details") as HTMLElement;
    expect(within(details).getByText(exact)).toBeInTheDocument();
  });

  it("unqualified joint accession and a wrong CIK are rendered honestly, not merged", async () => {
    await open(ACC, null);
    expect(
      within(section("Current EPS")).getByText("AMBIGUOUS"),
    ).toBeInTheDocument();
    expect(section("EPS change")).toHaveTextContent("INPUT_UNAVAILABLE");
    cleanup();

    await open(ACC, "0000999999");
    expect(
      within(section("Current EPS")).getByText("NOT_INGESTED"),
    ).toBeInTheDocument();
    expect(heading()).toHaveTextContent("Requested CIK 0000999999");
    expect(heading()).toHaveTextContent(
      "request echo, not a stored registrant",
    );
    expect(document.body.textContent).not.toContain("1.5300");
  });

  it("current refusal: MISSING flows to CURRENT_UNAVAILABLE and INPUT_UNAVAILABLE with no value", async () => {
    await open("0000320193-24-000060", CIK_A);
    expect(
      within(section("Current EPS")).getByText("MISSING"),
    ).toBeInTheDocument();
    expect(within(section("Current EPS")).queryByText("Value")).toBeNull();
    expect(
      within(section("Comparative EPS")).getByText("CURRENT_UNAVAILABLE"),
    ).toBeInTheDocument();
    expect(
      within(section("Comparative EPS")).getAllByText(/current EPS is MISSING/)[0] as HTMLElement,
    ).toBeInTheDocument();
    expect(
      within(section("EPS change")).getByText(/Not computed/),
    ).toBeInTheDocument();
    expect(within(section("EPS change")).queryByText(/%/)).toBeNull();
  });

  it("comparative period mismatch keeps its exact status and reason", async () => {
    await open("0000320193-24-000020", CIK_A);
    const comparative = section("Comparative EPS");
    expect(
      within(comparative).getByText("COMPARATIVE_PERIOD_MISMATCH"),
    ).toBeInTheDocument();
    const entry = GOLDEN.eps.find(
      (e) => e.request.accession === "0000320193-24-000020",
    ) as Golden["eps"][number];
    const reason = (entry.response.comparative as { reason: string }).reason;
    expect(reason.length).toBeGreaterThan(10);
    expect(within(comparative).getByText(reason)).toBeInTheDocument();
    expect(section("EPS change")).toHaveTextContent("INPUT_UNAVAILABLE");
  });

  it("null facts are distinct from missing and are never rendered as zero", async () => {
    await open("0000320193-24-000050", CIK_A);
    const comparative = section("Comparative EPS");
    expect(
      within(comparative).getByText("COMPARATIVE_NULL_VALUE"),
    ).toBeInTheDocument();
    expect(within(comparative).getByText(/null, not zero/)).toBeInTheDocument();
    expect(comparative.textContent).not.toMatch(/\b0(\.0+)?\b(?!\d)/);
    cleanup();

    await open("0000320193-24-000090", CIK_A);
    const current = section("Current EPS");
    expect(within(current).getByText("NULL_VALUE")).toBeInTheDocument();
    expect(within(current).getByText(/null, not zero/)).toBeInTheDocument();
    expect(current.textContent).not.toMatch(/\b0(\.0+)?\b(?!\d)/);
  });

  it("zero comparative: change and transition stay, the percentage is undefined, never 0%", async () => {
    await open("0000320193-24-000030", CIK_A);
    const change = section("EPS change");
    expect(within(change).getByText("+0.5000")).toBeInTheDocument();
    expect(within(change).getByText("Zero EPS to profit")).toBeInTheDocument();
    expect(
      within(change).getByText(/not defined for this comparative base/),
    ).toBeInTheDocument();
    expect(within(change).getAllByText(/The comparative EPS is zero\./).length).toBeGreaterThan(0);
    expect(change.textContent).not.toMatch(/%/);
    expect(
      within(section("Comparative EPS")).getByText("0.0000"),
    ).toBeInTheDocument();
  });

  it("negative comparative: signed change and transition stay, no absolute-denominator percentage", async () => {
    await open("0000320193-24-000040", CIK_A);
    const change = section("EPS change");
    expect(within(change).getByText("+0.5000")).toBeInTheDocument();
    expect(
      within(change).getByText("Loss per share narrowed"),
    ).toBeInTheDocument();
    expect(within(change).getAllByText(/The comparative EPS is negative\./).length).toBeGreaterThan(0);
    expect(change.textContent).not.toMatch(/%/);
  });

  it("profit to loss (annual) shows the API ratio neutrally, with no evaluative colour", async () => {
    await open("0000320193-23-000106", CIK_A, "ANNUAL");
    const change = section("EPS change");
    expect(within(change).getByText("Profit to loss")).toBeInTheDocument();
    expect(within(change).getByText("-1.5000")).toBeInTheDocument();
    expect(within(change).getByText("-150.00%")).toBeInTheDocument();
    // the workspace shell's connection dot is system status; the accounting content is neutral
    const html = [change, section("Current EPS"), section("Comparative EPS")]
      .map((el) => el.innerHTML)
      .join("");
    expect(html).not.toMatch(
      /status-ok|status-down|text-green|text-red|bg-green|bg-red/,
    );
  });

  it("a ratio that rounds to 0.00% is flagged non-zero and its exact value is kept", async () => {
    await open("0000320193-24-000080", CIK_A);
    const change = section("EPS change");
    // PercentValue flags it beside the figure; the exact ratio stays for assistive technology.
    expect(within(change).getByText(/\(not zero\)/)).toBeInTheDocument();
    expect(within(change).getByText(/exact ratio 0\.000001/)).toBeInTheDocument();
    const details = screen
      .getByText("Evidence and exact values")
      .closest("details") as HTMLElement;
    expect(within(details).getByText("0.000001")).toBeInTheDocument();
    expect(within(details).getByText("0.0001")).toBeInTheDocument();
  });

  it("an unsupported form/period and an explicit BASIC_AND_DILUTED are answers, not fallbacks", async () => {
    await open(ACC, CIK_A, "ANNUAL");
    expect(
      within(section("Current EPS")).getByText("UNSUPPORTED_FORM_PERIOD"),
    ).toBeInTheDocument();
    cleanup();
    vi.mocked(fetch).mockClear();
    await open(ACC, CIK_A, "QUARTER", "BASIC_AND_DILUTED");
    expect(
      within(section("Current EPS")).getByText("MISSING"),
    ).toBeInTheDocument();
    expect(epsCalls()).toHaveLength(1);
    expect(epsCalls()[0]).toContain("eps_kind=BASIC_AND_DILUTED");
  });

  it("an unknown accession is NOT_INGESTED, distinct from an empty index", async () => {
    await open("0000111111-24-000001", null);
    expect(
      within(section("Current EPS")).getByText("NOT_INGESTED"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("every state keeps the persistent no-PIT boundary", async () => {
    for (const entry of GOLDEN.eps) {
      const r = entry.request;
      await open(r.accession, r.cik, r.period_class, r.eps_kind);
      const banner = screen.getByRole("note", { name: "Data boundary" });
      expect(banner).toHaveTextContent(
        "Descriptive accounting data from SEC FSDS.",
      );
      expect(banner).toHaveTextContent("No point-in-time availability claim.");
      expect(banner).toHaveTextContent("Not a trading signal.");
      cleanup();
    }
  });
});

describe("large page sanity", () => {
  const rows = (count: number) =>
    Array.from({ length: count }, (_, n) =>
      makeFiling({
        accession: `0001000000-24-${String(n).padStart(6, "0")}`,
        company_cik: `${1_000_000 + (n % 23)}`.padStart(10, "0"),
      }),
    );

  it("decodes and renders the API's largest page (200 rows), keeping every row", () => {
    const items = rows(200);
    expect(parseFilingIndexPage(filingsPage(items, 450, 0, 200)).items).toHaveLength(200);
    render(<FilingsTable filings={items} offset={0} />);

    const links = within(screen.getByRole("table")).getAllByRole("link");
    expect(links).toHaveLength(200);
    expect(new Set(links.map((a) => a.getAttribute("href"))).size).toBe(200);
  });

  it("renders the page the UI actually requests (50 rows of 450) with a correct Next link", async () => {
    const items = rows(50);
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) =>
        Promise.resolve(
          jsonResponse(new URL(input).pathname === "/api/v1/filings" ? filingsPage(items, 450, 0, 50) : HEALTH),
        ),
      ),
    );
    await renderServer(await FilingsPage({ searchParams: Promise.resolve({}) } as never));

    expect(within(screen.getByRole("table")).getAllByRole("link")).toHaveLength(50);
    expect(screen.getByTestId("pagination-position")).toHaveTextContent("Rows 1–50 of 450 stored filings");
    expect(
      within(screen.getByRole("navigation", { name: "Pagination" })).getByRole("link", { name: "Next page" }),
    ).toHaveAttribute("href", "/filings?page=2");
  });

  it("refuses a page larger than the limit it was asked for, instead of showing it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(filingsPage(rows(200), 450, 0, 200)))),
    );
    await renderServer(await FilingsPage({ searchParams: Promise.resolve({}) } as never));

    expect(screen.getByRole("alert")).toHaveTextContent("does not understand");
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("language and provider audit of the accounting UI sources", () => {
  const FILES = [
    "src/app/filings/page.tsx",
    "src/app/filings/[accession]/page.tsx",
    "src/components/filings/eps-panels.tsx",
    "src/components/filings/filings-table.tsx",
    "src/lib/filings-view.ts",
    "src/lib/api/filings.ts",
    "src/lib/api/decode.ts",
    "src/lib/api/decoders/filings.ts",
    "src/lib/api/schema.ts",
  ];
  const text = (file: string) =>
    readFileSync(join(process.cwd(), file), "utf8")
      // comments may disclaim trading language ("no bullish/bearish"); only code and text count
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
      .replace(/Not a trading signal\./g, "")
      .toLowerCase();

  it.each(FILES)(
    "%s has no trading wording and no point-in-time claim",
    (file) => {
      const source = text(file);
      expect(source).not.toMatch(/\b(bullish|bearish|buy|sell|beat|miss)\b/);
      expect(source).not.toMatch(/\bsignal\b/);
      for (const phrase of [
        "known at filing",
        "available at acceptance",
        "tradable then",
        "point-in-time safe",
        "pit safe",
      ]) {
        expect(source).not.toContain(phrase);
      }
    },
  );

  it.each(FILES)(
    "%s calls no provider, no raw fetch and no mutating verb",
    (file) => {
      const source = text(file);
      expect(source).not.toMatch(/sec\.gov|massive|finnhub/);
      expect(source).not.toMatch(/\bfetch\(/);
      expect(source).not.toMatch(/method:\s*["'](post|put|patch|delete)/);
    },
  );
});
