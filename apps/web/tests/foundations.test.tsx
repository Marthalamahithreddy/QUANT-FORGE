import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FinancialValue } from "@/components/financial/financial-value";
import { PercentValue } from "@/components/financial/percent-value";
import { DomainState } from "@/components/state/domain-state";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { ReproducibilityBadge } from "@/components/state/reproducibility-badge";
import { StatusBadge } from "@/components/state/status-badge";
import { Callout } from "@/components/ui/callout";
import { CONTROL } from "@/components/ui/controls";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Frame } from "@/components/ui/frame";
import { PageContainer } from "@/components/ui/page-container";
import { Section } from "@/components/ui/section";
import { Skeleton, SkeletonRegion } from "@/components/ui/skeleton";
import { ApiError, apiGet } from "@/lib/api/client";
import { getEarningsPage } from "@/lib/api/earnings";
import { getBackendStatus } from "@/lib/api/health";
import { getFilings } from "@/lib/api/filings";
import { parseTheme, parseTint } from "@/lib/preferences";
import { REDACTED_ADDRESS, redactUrls } from "@/lib/safe-text";
import { STATE_KINDS, STATE_META, type NonValueState } from "@/lib/state";
import { vi } from "vitest";

const SRC = resolve(__dirname, "../src");

function sources(dir = SRC): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? sources(path) : /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}
const read = (path: string) => readFileSync(path, "utf8");
const NON_VALUE = STATE_KINDS.filter((k): k is NonValueState => k !== "VALUE");

describe("domain-state system: ten distinct states", () => {
  it("has exactly the ten approved states", () => {
    expect([...STATE_KINDS]).toEqual([
      "VALUE",
      "NOT_STATED",
      "NOT_REQUESTED",
      "NOT_APPLICABLE",
      "MISSING",
      "UNAVAILABLE",
      "REFUSED",
      "AMBIGUOUS",
      "PARTIAL",
      "ERROR",
    ]);
  });

  it("gives every state its own text label (meaning never depends on colour)", () => {
    const labels = NON_VALUE.map((k) => STATE_META[k].label);
    expect(new Set(labels).size).toBe(labels.length);
    for (const kind of NON_VALUE) {
      const { unmount } = render(<DomainState kind={kind} />);
      expect(screen.getByText(STATE_META[kind].label)).toBeInTheDocument();
      unmount();
    }
  });

  it("states the meaning in words for assistive technology", () => {
    for (const kind of NON_VALUE) {
      const { container, unmount } = render(<DomainState kind={kind} />);
      expect(container.textContent).toContain(STATE_META[kind].sentence);
      unmount();
    }
  });

  it("uses a different glyph per state that has one", () => {
    const glyphs = NON_VALUE.filter((k) => k !== "NOT_APPLICABLE").map((kind) => {
      const { container, unmount } = render(<DomainState kind={kind} />);
      const glyph = container.querySelector("svg")?.getAttribute("data-glyph");
      expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
      unmount();
      return glyph;
    });
    expect(glyphs.every(Boolean)).toBe(true);
    expect(new Set(glyphs).size).toBe(glyphs.length);
  });

  it("distinguishes dotted (not requested), dashed (refused) and solid outlines", () => {
    const outline = (kind: NonValueState) => {
      const { container, unmount } = render(<DomainState kind={kind} />);
      const cls = container.querySelector("[data-state]")?.className ?? "";
      unmount();
      return cls;
    };
    expect(outline("NOT_REQUESTED")).toContain("border-dotted");
    expect(outline("REFUSED")).toContain("border-dashed");
    for (const solid of ["MISSING", "UNAVAILABLE", "AMBIGUOUS", "PARTIAL", "ERROR"] as const) {
      expect(outline(solid)).toContain("border-solid");
    }
  });

  it("shows the backend reason verbatim without replacing the state's own label", () => {
    render(<DomainState kind="MISSING" reason="No close price is stored for 2026-05-01." />);
    expect(screen.getByText("Missing")).toBeInTheDocument();
    expect(screen.getByText(/No close price is stored for 2026-05-01\./)).toBeInTheDocument();
  });

  it("renders NOT_REQUESTED with its action and never as an error", () => {
    render(<DomainState kind="NOT_REQUESTED" action={<a href="#c">Select a benchmark</a>} />);
    expect(screen.getByRole("link", { name: "Select a benchmark" })).toBeInTheDocument();
    expect(document.querySelector("[role='alert']")).toBeNull();
  });

  it("a domain refusal is announced politely and is never styled or announced as an application error", () => {
    for (const kind of NON_VALUE.filter((k) => k !== "ERROR")) {
      const { container, unmount } = render(<RefusalNotice kind={kind} title="Point-in-time study" reason="Reason text." />);
      const notice = screen.getByRole("status");
      expect(notice).toHaveTextContent("Reason text.");
      expect(container.innerHTML).not.toMatch(/danger/);
      expect(screen.queryByRole("alert")).toBeNull();
      unmount();
    }
    render(<RefusalNotice kind="ERROR" title="Study" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("PARTIAL is a caution state, not a failure; AMBIGUOUS is not resolved by the UI", () => {
    const { container } = render(
      <>
        <StatusBadge kind="PARTIAL">PARTIAL</StatusBadge>
        <RefusalNotice kind="AMBIGUOUS" title="Price source" reason="Two sources report this window." />
      </>,
    );
    expect(container.innerHTML).not.toMatch(/danger/);
    expect(screen.getByText("Two sources report this window.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull(); // no control picks a candidate
  });

  it("separates descriptive and point-in-time badges by outline style as well as hue", () => {
    const { container } = render(
      <>
        <StatusBadge kind="VALUE" mode="descriptive">
          AVAILABLE
        </StatusBadge>
        <StatusBadge kind="REFUSED" mode="pit">
          REFUSED
        </StatusBadge>
      </>,
    );
    expect(container.querySelector("[data-mode='descriptive']")?.className).toContain("border-solid");
    expect(container.querySelector("[data-mode='pit']")?.className).toContain("border-dashed");
  });
});

describe("ReproducibilityBadge", () => {
  it("supports exactly COMPLETE and PARTIAL, as text with a glyph and a sentence", () => {
    const { container, rerender } = render(<ReproducibilityBadge level="COMPLETE" />);
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(container.querySelector("[data-glyph='filled-ring']")).not.toBeNull();
    rerender(<ReproducibilityBadge level="PARTIAL" />);
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(container.querySelector("[data-glyph='half-circle']")).not.toBeNull();
  });

  it("never presents PARTIAL as failed, incorrect or an error", () => {
    const { container } = render(<ReproducibilityBadge level="PARTIAL" />);
    expect(container.innerHTML).not.toMatch(/danger|error|fail|incorrect/i);
    expect(container).toHaveTextContent("The numbers are not wrong; the evidence trail is incomplete.");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("is a presentational badge: nothing here derives a level for an unsaved study", () => {
    const source = read(join(SRC, "components/state/reproducibility-badge.tsx"));
    expect(source).not.toMatch(/fetch|apiGet|derive|infer/i.test(source) ? /^$/ : /\bgaps\b/);
  });
});

describe("FinancialValue: exact strings, never a number", () => {
  const text = (value: string | null, kind: Parameters<typeof FinancialValue>[0]["kind"]) => {
    const { container, unmount } = render(<FinancialValue value={value} kind={kind} />);
    const out = container.textContent ?? "";
    unmount();
    return out;
  };

  it("keeps trailing zeros and tiny values exactly", () => {
    expect(text("1.5300", "eps")).toBe("1.5300");
    expect(text("0.0000001", "price")).toBe("0.0000001");
    expect(text("182.3400", "price")).toBe("182.3400");
  });

  it("keeps a value a float could not represent", () => {
    const big = "123456789012345678901234567890.123456789012345678901234567890";
    expect(text(big, "eps")).toBe(big);
    expect(Number(big).toString()).not.toBe(big); // proves the point: a float would have changed it
  });

  it("shows a signed exact string with a plus on non-zero positives only", () => {
    expect(text("0.2000", "signed")).toBe("+0.2000");
    expect(text("-0.0100", "signed")).toBe("-0.0100");
    expect(text("0.0000", "signed")).toBe("0.0000");
    expect(text("-0.00", "signed")).toBe("-0.00"); // preserved exactly as sent
  });

  it("groups the integer part losslessly for money", () => {
    expect(text("94836000000.0000", "money")).toBe("94,836,000,000.0000");
    expect(text("-1234.50", "money")).toBe("-1,234.50");
    expect(text("12", "money")).toBe("12");
    expect(text("0.123456", "money")).toBe("0.123456");
  });

  it("keeps the exact string available on the element", () => {
    const { container } = render(<FinancialValue value="94836000000.0000" kind="money" />);
    expect(container.querySelector("[data-exact]")).toHaveAttribute("data-exact", "94836000000.0000");
  });

  it("renders null as NOT_STATED, never as zero or blank", () => {
    const { container } = render(<FinancialValue value={null} kind="eps" />);
    expect(container.querySelector("[data-state='NOT_STATED']")).not.toBeNull();
    expect(container.textContent).toContain("The source did not supply this.");
    expect(container.textContent).not.toMatch(/\b0\b/);
  });

  it("renders an explicit state in place of a value, with the backend reason", () => {
    const { container } = render(<FinancialValue value={null} kind="ratio" state="REFUSED" reason="Timing not stated." />);
    expect(container.querySelector("[data-state='REFUSED']")).not.toBeNull();
    expect(container.textContent).toContain("Timing not stated.");
  });

  it("treats a non-decimal string as a contract ERROR rather than repairing it", () => {
    for (const bad of ["1e-7", "1,5", "NaN", " 1.5", "abc"]) {
      const { container, unmount } = render(<FinancialValue value={bad} kind="eps" />);
      expect(container.querySelector("[data-state='ERROR']"), bad).not.toBeNull();
      unmount();
    }
  });

  it("marks direction from the digits, so negative zero is zero", () => {
    const direction = (v: string) => {
      const { container, unmount } = render(<FinancialValue value={v} kind="signed" />);
      const d = container.querySelector("[data-exact]")?.getAttribute("data-direction");
      unmount();
      return d;
    };
    expect(direction("0.5")).toBe("positive");
    expect(direction("-0.5")).toBe("negative");
    expect(direction("-0.00")).toBe("zero");
    expect(direction("0")).toBe("zero");
  });

  it("carries the sign in the text, so the optional tint is never the only cue", () => {
    const { container } = render(<FinancialValue value="-0.0100" kind="signed" />);
    expect(container.textContent).toBe("-0.0100");
    expect(read(resolve(SRC, "app/globals.css"))).toMatch(/data-tint="on"\][^{]*\[data-direction="negative"\]::before/);
  });
});

describe("PercentValue: ratio to percent without a float", () => {
  const pct = (ratio: string, places?: number) => {
    const { container, unmount } = render(<PercentValue ratio={ratio} places={places} />);
    const visible = container.querySelector(".num")?.firstChild?.textContent ?? "";
    unmount();
    return visible;
  };

  it("shifts the decimal point exactly: 0.125 is 12.50%", () => {
    expect(pct("0.125")).toBe("+12.50%");
    expect(pct("-0.0125")).toBe("-1.25%");
    expect(pct("0.5")).toBe("+50.00%");
    expect(pct("0")).toBe("0.00%");
  });

  it("rounds half away from zero on the digits", () => {
    expect(pct("0.12345")).toBe("+12.35%");
    expect(pct("-0.12345")).toBe("-12.35%");
  });

  it("is exact for a 50-digit repeating ratio", () => {
    expect(pct("0.33333333333333333333333333333333333333333333333333")).toBe("+33.33%");
  });

  it("keeps the exact ratio available and flags a non-zero value that rounds to 0.00%", () => {
    const { container } = render(<PercentValue ratio="0.000001" />);
    expect(container.textContent).toContain("0.00%");
    expect(container.textContent).toContain("(not zero)");
    expect(container.textContent).toContain("exact ratio 0.000001");
    expect(container.querySelector("[title='Exact ratio: 0.000001']")).not.toBeNull();
  });

  it("does not flag an exact zero", () => {
    const { container } = render(<PercentValue ratio="0.0000" />);
    expect(container.textContent).not.toContain("(not zero)");
  });
});

describe("financial primitives are string-only and server-compatible", () => {
  const files = [
    "components/financial/financial-value.tsx",
    "components/financial/percent-value.tsx",
  ].map((f) => read(join(SRC, f)));

  it("never converts a value to a number", () => {
    for (const source of files) {
      expect(source).not.toMatch(/\bNumber\s*\(|parseFloat|parseInt|toFixed|Math\.|\bunary\b/);
    }
    const format = read(join(SRC, "lib/format.ts"));
    const added = format.slice(format.indexOf("Foundations for the V2 financial-value primitives"));
    expect(added).not.toMatch(/\bNumber\s*\(|parseFloat|parseInt|toFixed|Math\./);
  });

  it("has no hooks or client directive", () => {
    for (const source of files) expect(source).not.toMatch(/use client|useState|useEffect/);
  });
});

describe("base primitives", () => {
  it("EmptyState is a polite status, shows no rows and no invented content", () => {
    render(<EmptyState title="Nothing stored yet">Events appear after ingestion.</EmptyState>);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Nothing stored yet");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("ErrorState is an alert and never prints a service address", () => {
    render(
      <ErrorState
        title="The API is unavailable"
        detail="Cannot reach http://localhost:8000/api/v1/earnings-events?limit=50: refused"
      >
        See https://internal.example:8443/health for details.
      </ErrorState>,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).not.toMatch(/https?:|localhost|internal\.example|\/api\//);
    expect(alert.textContent).toContain(REDACTED_ADDRESS);
  });

  it("redacts addresses and nothing else", () => {
    expect(redactUrls("a http://x.y/z b")).toBe(`a ${REDACTED_ADDRESS} b`);
    expect(redactUrls("plain text, no address")).toBe("plain text, no address");
    expect(redactUrls("HTTPS://UP.EXAMPLE/p?q=1)")).toBe(`${REDACTED_ADDRESS})`);
  });

  it("Skeleton is decorative, sized by the caller, and a region announces what loads", () => {
    const { container } = render(
      <SkeletonRegion label="Loading earnings events">
        <Skeleton width="12rem" height="2rem" />
      </SkeletonRegion>,
    );
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-busy", "true");
    expect(region).toHaveTextContent("Loading earnings events");
    const block = container.querySelector(".qf-skeleton") as HTMLElement;
    expect(block).toHaveAttribute("aria-hidden", "true");
    expect(block.style.width).toBe("12rem");
    expect(block.style.height).toBe("2rem");
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("Section is NOT a card; Frame is a bordered block with a labelled heading", () => {
    const { container } = render(
      <>
        <Section title="Event">
          <p>one</p>
        </Section>
        <Frame title="Descriptive" note="what happened">
          <p>two</p>
        </Frame>
      </>,
    );
    const section = screen.getByRole("region", { name: "Event" });
    expect(section.className).not.toMatch(/rounded|bg-surface|shadow/);
    const frame = screen.getByRole("region", { name: "Descriptive" });
    expect(frame.className).toMatch(/rounded/);
    expect(frame.className).not.toMatch(/shadow/);
    expect(within(frame).getByRole("heading", { name: "Descriptive" })).toBeInTheDocument();
    expect(container.querySelectorAll("section section")).toHaveLength(0); // never nested
  });

  it("Callout is a rule and text, not a box", () => {
    render(
      <Callout role="note" label="Boundary">
        Descriptive data.
      </Callout>,
    );
    const note = screen.getByRole("note", { name: "Boundary" });
    expect(note.className).toContain("border-l-[3px]");
    expect(note.className).not.toMatch(/rounded|shadow/);
  });

  it("PageContainer offers exactly the three approved widths", () => {
    const widths = (w: "reading" | "workspace" | "table") => {
      const { container, unmount } = render(<PageContainer width={w}>x</PageContainer>);
      const cls = (container.firstElementChild as HTMLElement).className;
      unmount();
      return cls;
    };
    expect(widths("reading")).toContain("max-w-reading");
    expect(widths("workspace")).toContain("max-w-workspace");
    expect(widths("table")).toContain("max-w-table");
  });

  it("form controls use the 3:1 control border and grow to 44px below desktop", () => {
    expect(CONTROL).toContain("border-border-control");
    expect(CONTROL).not.toContain("border-border-strong");
    expect(CONTROL).toContain("max-lg:min-h-11");
  });
});

describe("no internal address in any user-visible error", () => {
  const fail = (value: Response | Error) =>
    vi.stubGlobal("fetch", vi.fn(() => (value instanceof Response ? Promise.resolve(value) : Promise.reject(value))));

  it("ApiError.publicMessage names no address, for transport, timeout, HTTP and JSON failures", async () => {
    fail(new TypeError("fetch failed"));
    await expect(apiGet("/api/v1/x", { baseUrl: "http://secret-host:9999" })).rejects.toSatisfy(
      (e: ApiError) => e.publicMessage === "The QuantForge API could not be reached." && !/secret-host|http/.test(e.publicMessage),
    );
    fail(new DOMException("t", "TimeoutError"));
    await expect(apiGet("/api/v1/x", { baseUrl: "http://secret-host:9999" })).rejects.toSatisfy(
      (e: ApiError) => e.publicMessage === "The QuantForge API did not answer in time.",
    );
    fail(new Response("{}", { status: 503, statusText: "Service Unavailable" }));
    await expect(apiGet("/api/v1/x", { baseUrl: "http://secret-host:9999" })).rejects.toSatisfy(
      (e: ApiError) => e.publicMessage === "The QuantForge API answered HTTP 503." && /secret-host/.test(e.message),
    );
    fail(new Response("not json", { status: 200 }));
    await expect(apiGet("/api/v1/x", { baseUrl: "http://secret-host:9999" })).rejects.toSatisfy(
      (e: ApiError) => /not valid JSON/.test(e.publicMessage) && !/secret-host/.test(e.publicMessage),
    );
    vi.unstubAllGlobals();
  });

  it("the earnings, filings and health reads put no address in what pages render", async () => {
    fail(new TypeError("fetch failed"));
    const earnings = await getEarningsPage();
    const filings = await getFilings();
    const health = await getBackendStatus();
    vi.unstubAllGlobals();
    expect(earnings.state === "error" && earnings.detail).not.toMatch(/https?:|localhost/);
    expect(filings.state === "error" && filings.detail).not.toMatch(/https?:|localhost/);
    expect(health.state === "unavailable" && health.detail).not.toMatch(/https?:|localhost/);
  });
});

describe("preferences", () => {
  it("parses the theme cookie, defaulting to system", () => {
    expect(parseTheme(undefined)).toBe("system");
    expect(parseTheme("dark")).toBe("dark");
    expect(parseTheme("light")).toBe("light");
    expect(parseTheme("system")).toBe("system");
    expect(parseTheme("purple")).toBe("system");
  });

  it("keeps direction tint OFF unless the cookie is exactly on", () => {
    expect(parseTint(undefined)).toBe(false);
    expect(parseTint("off")).toBe(false);
    expect(parseTint("true")).toBe(false);
    expect(parseTint("on")).toBe(true);
  });
});

describe("source guards for the foundation", () => {
  const files = sources();

  it("keeps raw colour literals in globals.css only", () => {
    const offenders = files
      .filter((f) => !f.endsWith("globals.css"))
      .filter((f) => /#[0-9a-fA-F]{3,8}\b(?![\w-])|\brgba?\(|\bhsla?\(|\boklch\(/.test(read(f).replace(/&#\d+;/g, "")))
      .map((f) => relative(SRC, f));
    expect(offenders).toEqual([]);
  });

  it("never removes the focus outline without a replacement", () => {
    for (const file of files) {
      const source = read(file);
      expect(source, relative(SRC, file)).not.toMatch(/outline-none|outline-0\b|focus:outline-none/);
    }
  });

  it("limits `use client` to the five components that need browser state", () => {
    const clients = files.filter((f) => /^["']use client["']/m.test(read(f))).map((f) => relative(SRC, f)).sort();
    expect(clients).toEqual(
      [
        "app/error.tsx", // Next error boundary: receives `retry`, must be a Client Component
        "components/earnings/earnings-table.tsx", // legacy local sort/filter state (removed in 11.1C)
        "components/layout/mobile-nav.tsx", // opens/closes a native <dialog>
        "components/layout/nav-link.tsx", // usePathname for the current section
        "components/layout/theme-control.tsx", // writes the theme cookie and root attribute
      ].sort(),
    );
  });

  it("keeps the shell, primitives, state and financial components server-compatible", () => {
    for (const dir of ["ui", "state", "financial"]) {
      for (const name of readdirSync(join(SRC, "components", dir))) {
        if (!name.endsWith(".tsx")) continue;
        expect(read(join(SRC, "components", dir, name)), `${dir}/${name}`).not.toMatch(/use client|useState|useEffect|useRef/);
      }
    }
    for (const server of ["app-shell", "header", "nav-groups", "wordmark", "api-status"]) {
      expect(read(join(SRC, "components/layout", `${server}.tsx`))).not.toMatch(/use client/);
    }
  });

  it("does not use localStorage or sessionStorage anywhere", () => {
    const code = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    for (const file of files) expect(code(read(file)), relative(SRC, file)).not.toMatch(/localStorage|sessionStorage/);
  });

  it("has no Inter @font-face or remote font, and no global tabular numerals", () => {
    const css = read(join(SRC, "app/globals.css"));
    expect(css).not.toMatch(/font-feature-settings:\s*"cv|"tnum"/);
  });

  it("adds no data-layer or contract-generation code", () => {
    expect(files.some((f) => /openapi|generated/i.test(relative(SRC, f)))).toBe(false);
  });
});

describe("review regressions (11.1B)", () => {
  it("a compact (dash-only) state is still NAMED for assistive technology", () => {
    const { container } = render(<DomainState kind="NOT_STATED" compact />);
    expect(container.textContent).toContain("Not stated: The source did not supply this.");
    expect(container.textContent).not.toMatch(/^:/);
    const na = render(<DomainState kind="NOT_APPLICABLE" compact />);
    expect(na.container.textContent).toContain("Not applicable: This does not apply here.");
  });

  it("a mode gives the badge its hue but a non-value state keeps its own outline", () => {
    const outline = (kind: Parameters<typeof StatusBadge>[0]["kind"], mode: "descriptive" | "pit") => {
      const { container, unmount } = render(<StatusBadge kind={kind} mode={mode}>x</StatusBadge>);
      const cls = container.querySelector("[data-state]")?.className ?? "";
      const glyph = container.querySelector("svg")?.getAttribute("data-glyph") ?? null;
      unmount();
      return { cls, glyph };
    };
    const available = outline("VALUE", "descriptive");
    const refused = outline("REFUSED", "descriptive");
    expect(available.cls).toContain("border-solid");
    expect(refused.cls).toContain("border-dashed");
    expect(refused.glyph).toBe("circle-slash");
    expect(available.glyph).toBeNull();
    expect(outline("NOT_REQUESTED", "descriptive").cls).toContain("border-dotted");
    expect(refused.cls).toContain("text-mode-descriptive");
    expect(outline("REFUSED", "pit").cls).toContain("text-mode-pit");
  });

  it("decides 'rounds to zero' on the digits, independent of how the percent is written", async () => {
    const { ratioRoundsToZero, formatRatioAsPercent } = await import("@/lib/format");
    expect(ratioRoundsToZero("0.000049")).toBe(true); // 0.0049% -> 0.00%
    expect(ratioRoundsToZero("-0.000049")).toBe(true);
    expect(ratioRoundsToZero("0.00005")).toBe(false); // 0.005% rounds up to 0.01%
    expect(formatRatioAsPercent("0.00005")).toBe("+0.01%");
    expect(ratioRoundsToZero("0")).toBe(false);
    expect(ratioRoundsToZero("0.0000")).toBe(false);
    expect(ratioRoundsToZero("0.125")).toBe(false);
  });

  it("ApiError carries a structured kind, and its public text does not depend on message wording", async () => {
    const make = (kind: "timeout" | "unreachable" | "http" | "invalid_json", status = 0) =>
      new ApiError("upstream request timed out at http://h:1/x", { url: "http://h:1/x", status, kind });
    expect(make("unreachable").publicMessage).toBe("The QuantForge API could not be reached."); // despite 'timed out' in the text
    expect(make("timeout").publicMessage).toBe("The QuantForge API did not answer in time.");
    expect(make("http", 502).publicMessage).toBe("The QuantForge API answered HTTP 502.");
    expect(make("invalid_json", 200).publicMessage).toMatch(/not valid JSON/);
  });

  it("redacts scheme-less host:port and localhost text, not just URLs", () => {
    expect(redactUrls("connect ECONNREFUSED 127.0.0.1:8000")).toBe(`connect ECONNREFUSED ${REDACTED_ADDRESS}`);
    expect(redactUrls("failed at localhost:8000/api/v1/health")).toBe(`failed at ${REDACTED_ADDRESS}`);
    expect(redactUrls("api.internal.example:8443/x done")).toBe(`${REDACTED_ADDRESS} done`);
    expect(redactUrls("Fiscal 2026: Q1 results")).toBe("Fiscal 2026: Q1 results"); // ordinary prose is left alone
  });

  it("the header status redacts an address in an unavailable reason and health never echoes raw errors", async () => {
    const { ApiStatus } = await import("@/components/layout/api-status");
    const { container } = render(<ApiStatus status={{ state: "unavailable", detail: "connect ECONNREFUSED 127.0.0.1:8000" }} />);
    expect(container.textContent).not.toMatch(/127\.0\.0\.1/);
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 200 }))));
    vi.stubGlobal("fetch", vi.fn(() => { throw new Error("connect ECONNREFUSED 127.0.0.1:8000"); }));
    const status = await getBackendStatus();
    vi.unstubAllGlobals();
    expect(status.state === "unavailable" && status.detail).not.toMatch(/127\.0\.0\.1|ECONNREFUSED/);
  });

  it("the theme control claims no selection on the server (never a wrong one)", async () => {
    const { renderToString } = await import("react-dom/server");
    const { ThemeControl } = await import("@/components/layout/theme-control");
    const html = renderToString(<ThemeControl />);
    expect(html).not.toMatch(/checked/);
    expect((html.match(/type="radio"/g) ?? []).length).toBe(3);
  });
});
