import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { NAV_GROUPS, NAV_ITEMS } from "@/lib/navigation";
import type { BackendStatus } from "@/types/api";

const pathname = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

const CONNECTED: BackendStatus = {
  state: "connected",
  health: { status: "ok", service: "quantforge-api", version: "0.1.0", environment: "local" },
};

const UNAVAILABLE: BackendStatus = {
  state: "unavailable",
  detail: "The QuantForge API could not be reached.",
};

afterEach(() => {
  pathname.current = "/";
  document.documentElement.removeAttribute("data-theme");
  document.cookie = "qf-theme=; path=/; max-age=0";
});

function shell(status: BackendStatus | null = CONNECTED, extra: object = {}) {
  return render(
    <AppShell status={status ?? undefined} {...extra}>
      <p>content</p>
    </AppShell>,
  );
}

describe("AppShell structure", () => {
  it("renders the QF/ identity once, as a link to Overview, with an accessible name", () => {
    shell();
    const brand = screen.getByRole("link", { name: "QuantForge" });
    expect(brand).toHaveAttribute("href", "/");
    expect(brand).toHaveTextContent("QF/");
  });

  it("renders children inside the main landmark, which is the skip-link target", () => {
    shell();
    const main = screen.getByRole("main");
    expect(main).toHaveAttribute("id", "main-content");
    expect(within(main).getByText("content")).toBeInTheDocument();
  });

  it("has a 44px (h-11) header strip and a 12.5rem rail", () => {
    const { container } = shell();
    expect(container.querySelector("header")?.className).toContain("h-11");
    expect(container.firstElementChild?.className).toContain("12.5rem");
  });

  it("exposes the primary navigation landmark, and a header landmark", () => {
    shell();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByRole("banner")).toBeInTheDocument();
  });
});

describe("navigation", () => {
  it("groups entries as Overview, Evidence (Earnings, Filings), Research (Research, Saved research)", () => {
    expect(NAV_GROUPS.map((g) => [g.label, g.items.map((i) => i.label)])).toEqual([
      [null, ["Overview"]],
      ["Evidence", ["Earnings", "Filings"]],
      ["Research", ["Research", "Saved research"]],
    ]);
    shell();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByText("Evidence")).toBeInTheDocument();
    expect(within(nav).getByText("Research", { selector: "p" })).toBeInTheDocument();
    for (const item of NAV_ITEMS) {
      expect(within(nav).getByRole("link", { name: item.label })).toHaveAttribute("href", item.href);
    }
  });

  it("has no Companies entry, no Backtests entry, and no Planned rows", () => {
    shell();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).queryByText(/Companies/)).toBeNull();
    expect(within(nav).queryByText(/Backtests/)).toBeNull();
    expect(screen.queryByText("Planned")).toBeNull();
    expect(nav.querySelectorAll("[aria-disabled]")).toHaveLength(0);
  });

  it("marks only the current section, by the most specific match", () => {
    pathname.current = "/research/saved";
    shell();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Saved research" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Research" })).not.toHaveAttribute("aria-current");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("marks a detail route's section as current", () => {
    pathname.current = "/earnings/abc";
    shell();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Earnings" })).toHaveAttribute("aria-current", "page");
  });

  it("marks Overview current only on the root route", () => {
    shell();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  });
});

describe("header: breadcrumbs, environment and API status", () => {
  it("shows breadcrumbs with the last item as the current page", () => {
    shell(CONNECTED, { breadcrumbs: [{ label: "Earnings", href: "/earnings" }, { label: "Acme · 2026-04-30" }] });
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Earnings" })).toHaveAttribute("href", "/earnings");
    expect(within(crumbs).getByText("Acme · 2026-04-30")).toHaveAttribute("aria-current", "page");
  });

  it("shows no breadcrumb landmark on a page without breadcrumbs", () => {
    shell();
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
  });

  it("shows a connected state as text, with the service detail for assistive technology", () => {
    shell();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("quantforge-api 0.1.0 (local)")).toBeInTheDocument();
  });

  it("shows the environment chip only when connected and not production", () => {
    shell();
    expect(screen.getByText("env: local")).toBeInTheDocument();
    shell({ state: "connected", health: { ...(CONNECTED as { health: object }).health, environment: "production" } } as BackendStatus);
    expect(screen.queryByText("env: production")).toBeNull();
  });

  it("shows an unavailable state with the safe reason and never reports connected", () => {
    shell(UNAVAILABLE);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByText(UNAVAILABLE.detail, { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("Connected")).toBeNull();
  });

  it("does NOT fabricate an environment when the backend is unavailable", () => {
    shell(UNAVAILABLE);
    expect(screen.queryByText(/^env:/)).toBeNull();
  });

  it("paints before any answer exists, as Checking API (never connected)", () => {
    shell(null);
    expect(screen.getByText("Checking API")).toBeInTheDocument();
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.queryByText("Unavailable")).toBeNull();
  });
});

describe("mobile navigation (below the desktop breakpoint)", () => {
  it("has a labelled menu button that opens a labelled modal dialog with the same links", async () => {
    const user = userEvent.setup();
    shell();
    const open = screen.getByRole("button", { name: "Open navigation" });
    expect(open).toHaveAttribute("aria-haspopup", "dialog");
    // closed: no second copy of the links in the page
    expect(screen.getAllByRole("link", { name: "Earnings" })).toHaveLength(1);

    await user.click(open);
    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    const menu = within(dialog).getByRole("navigation", { name: "Primary menu" });
    for (const item of NAV_ITEMS) {
      expect(within(menu).getByRole("link", { name: item.label })).toHaveAttribute("href", item.href);
    }
  });

  it("indicates the current route in the drawer and has 44px (min-h-11) targets", async () => {
    pathname.current = "/filings";
    const user = userEvent.setup();
    shell();
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    const menu = screen.getByRole("navigation", { name: "Primary menu" });
    const current = within(menu).getByRole("link", { name: "Filings" });
    expect(current).toHaveAttribute("aria-current", "page");
    expect(current.className).toContain("max-lg:min-h-11");
    expect(screen.getByRole("button", { name: "Open navigation" }).className).toContain("size-11");
    expect(screen.getByRole("button", { name: "Close navigation" }).className).toContain("size-11");
  });

  it("is dismissible: the close button, a link, and the backdrop all close it", async () => {
    const user = userEvent.setup();
    shell();
    const open = screen.getByRole("button", { name: "Open navigation" });

    await user.click(open);
    await user.click(screen.getByRole("button", { name: "Close navigation" }));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();

    await user.click(open);
    await user.click(within(screen.getByRole("navigation", { name: "Primary menu" })).getByRole("link", { name: "Filings" }));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();

    await user.click(open);
    await user.click(screen.getByRole("dialog", { name: "Navigation" }));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
  });

  it("offers the theme control inside the drawer for widths where the header has no room", async () => {
    const user = userEvent.setup();
    shell();
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    const group = within(dialog).getByRole("group", { name: "Theme" });
    await user.click(within(group).getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(within(group).getByRole("radio", { name: "Dark" })).toBeChecked();
  });

  it("closes when the viewport grows to the desktop layout", async () => {
    const listeners: Array<() => void> = [];
    const mq = {
      matches: false,
      addEventListener: (_: string, fn: () => void) => listeners.push(fn),
      removeEventListener: () => undefined,
    };
    vi.stubGlobal("matchMedia", () => mq);
    const user = userEvent.setup();
    shell();
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeInTheDocument();
    mq.matches = true;
    await act(async () => listeners.forEach((fn) => fn()));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
    vi.unstubAllGlobals();
  });

  it("closes when the route changes", async () => {
    const user = userEvent.setup();
    const { rerender } = shell();
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeInTheDocument();
    pathname.current = "/earnings";
    await act(async () => {
      rerender(
        <AppShell status={CONNECTED}>
          <p>content</p>
        </AppShell>,
      );
    });
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
  });
});

describe("theme control", () => {
  it("is a labelled radio group with System, Light and Dark, reflecting the current theme", () => {
    shell();
    const group = screen.getByRole("group", { name: "Theme" });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => (r as HTMLInputElement).value)).toEqual(["system", "light", "dark"]);
    expect(within(group).getByRole("radio", { name: "System" })).toBeChecked();
  });

  it("is keyboard operable and persists the choice in a server-readable cookie, not localStorage", async () => {
    const user = userEvent.setup();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    shell();
    const group = screen.getByRole("group", { name: "Theme" });

    await user.tab(); // reach the control with the keyboard
    const dark = within(group).getByRole("radio", { name: "Dark" });
    dark.focus();
    await user.keyboard(" ");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.cookie).toContain("qf-theme=dark");
    expect(within(group).getByRole("radio", { name: "Dark" })).toBeChecked();

    await user.click(within(group).getByRole("radio", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.cookie).toContain("qf-theme=light");

    await user.click(within(group).getByRole("radio", { name: "System" }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("reads the server-rendered attribute as its initial state", () => {
    document.documentElement.dataset.theme = "light";
    shell();
    expect(within(screen.getByRole("group", { name: "Theme" })).getByRole("radio", { name: "Light" })).toBeChecked();
  });
});
