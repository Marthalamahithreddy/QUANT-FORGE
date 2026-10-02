import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import { NAV_ITEMS } from "@/lib/navigation";
import type { BackendStatus } from "@/types/api";

const CONNECTED: BackendStatus = {
  state: "connected",
  health: {
    status: "ok",
    service: "quantforge-api",
    version: "0.1.0",
    environment: "local",
  },
};

const UNAVAILABLE: BackendStatus = {
  state: "unavailable",
  detail: "Cannot reach http://localhost:8000/api/v1/health: no response from the server",
};

describe("AppShell", () => {
  it("renders the QuantForge identity", () => {
    render(
      <AppShell status={CONNECTED}>
        <p>content</p>
      </AppShell>,
    );

    // Present in the top bar and again as the main heading target.
    expect(screen.getAllByText("QuantForge").length).toBeGreaterThan(0);
  });

  it("renders children inside the main landmark", () => {
    render(
      <AppShell status={CONNECTED}>
        <p>research terminal body</p>
      </AppShell>,
    );

    const main = screen.getByRole("main");
    expect(within(main).getByText("research terminal body")).toBeInTheDocument();
  });

  it("exposes primary navigation with a landmark label", () => {
    render(
      <AppShell status={CONNECTED}>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  });

  it("renders every declared navigation entry", () => {
    render(
      <AppShell status={CONNECTED}>
        <p>content</p>
      </AppShell>,
    );

    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const item of NAV_ITEMS) {
      expect(within(nav).getByText(item.label)).toBeInTheDocument();
    }
  });

  it("marks the available section as the current page", () => {
    render(
      <AppShell status={CONNECTED}>
        <p>content</p>
      </AppShell>,
    );

    const nav = screen.getByRole("navigation", { name: "Primary" });
    const current = within(nav).getByRole("link", { name: /Overview/ });

    expect(current).toHaveAttribute("aria-current", "page");
  });

  it("renders planned sections as disabled, non-navigable entries", () => {
    render(
      <AppShell status={CONNECTED}>
        <p>content</p>
      </AppShell>,
    );

    const nav = screen.getByRole("navigation", { name: "Primary" });
    const planned = NAV_ITEMS.filter((item) => item.status === "planned");

    expect(planned).toHaveLength(5);
    for (const item of planned) {
      // A disabled row must not be a link: no route exists to navigate to.
      expect(
        within(nav).queryByRole("link", { name: new RegExp(`^${item.label}`) }),
      ).toBeNull();
      const entry = within(nav).getByText(item.label).closest("[aria-disabled]");
      expect(entry).not.toBeNull();
      expect(entry).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("labels every planned entry as Planned", () => {
    render(
      <AppShell status={CONNECTED}>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getAllByText("Planned")).toHaveLength(5);
  });

  it("shows a connected backend state", () => {
    render(
      <AppShell status={CONNECTED}>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getAllByText("Connected").length).toBeGreaterThan(0);
    expect(
      screen.getByText("quantforge-api 0.1.0 (local)"),
    ).toBeInTheDocument();
  });

  it("shows an unavailable backend state with the real reason", () => {
    render(
      <AppShell status={UNAVAILABLE}>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(
      screen.getByText(UNAVAILABLE.detail, { exact: false }),
    ).toBeInTheDocument();
  });

  it("never reports connected when the backend is unavailable", () => {
    render(
      <AppShell status={UNAVAILABLE}>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.queryByText("Connected")).toBeNull();
  });

  it("falls back to the local environment label when disconnected", () => {
    render(
      <AppShell status={UNAVAILABLE}>
        <p>content</p>
      </AppShell>,
    );

    expect(screen.getByText("env: local")).toBeInTheDocument();
  });
});
