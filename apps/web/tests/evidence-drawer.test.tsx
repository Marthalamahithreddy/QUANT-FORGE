import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { EvidenceDrawer } from "@/components/ui/evidence-drawer";

const view = () =>
  render(
    <main>
      <EvidenceDrawer subject="the +5 session stock return" title="Stock return evidence">
        <p>Resolved listing 1234</p>
      </EvidenceDrawer>
    </main>,
  );

describe("EvidenceDrawer", () => {
  it("has a trigger whose accessible name says what it opens, and mounts no evidence while closed", () => {
    view();
    const trigger = screen.getByRole("button", { name: "Evidence for the +5 session stock return" });
    expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(screen.queryByText("Resolved listing 1234")).toBeNull();
  });

  it("opens a labelled modal dialog with the evidence and moves focus to Close", async () => {
    const user = userEvent.setup();
    view();
    await user.click(screen.getByRole("button", { name: /Evidence for/ }));
    const dialog = screen.getByRole("dialog", { name: "Stock return evidence" });
    expect(within(dialog).getByText("Resolved listing 1234")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close evidence" })).toHaveFocus();
  });

  it("closes with the Close button and returns focus to its trigger", async () => {
    const user = userEvent.setup();
    view();
    const trigger = screen.getByRole("button", { name: /Evidence for/ });
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Close evidence" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("Resolved listing 1234")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("closes on a backdrop click (a click on the dialog itself) and returns focus", async () => {
    const user = userEvent.setup();
    view();
    const trigger = screen.getByRole("button", { name: /Evidence for/ });
    await user.click(trigger);
    await user.click(screen.getByRole("dialog", { name: "Stock return evidence" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("closes on Escape (the platform's close event) and returns focus to the trigger", async () => {
    const user = userEvent.setup();
    view();
    const trigger = screen.getByRole("button", { name: /Evidence for/ });
    await user.click(trigger);
    // The native dialog fires `close` for Esc; jsdom has no Esc handling, so model the platform event.
    (document.querySelector("dialog") as HTMLDialogElement).close();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("can reopen after closing, and touch targets are 44px below the desktop breakpoint", async () => {
    const user = userEvent.setup();
    view();
    const trigger = screen.getByRole("button", { name: /Evidence for/ });
    expect(trigger.className).toContain("max-lg:min-h-11");
    await user.click(trigger);
    expect(screen.getByRole("button", { name: "Close evidence" }).className).toContain("size-11");
    await user.click(screen.getByRole("button", { name: "Close evidence" }));
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "Stock return evidence" })).toBeInTheDocument();
  });

  it("is a right-hand panel on wide screens and a full-height sheet on narrow ones", () => {
    view();
    const cls = document.querySelector("dialog")!.className;
    expect(cls).toMatch(/right-0/);
    expect(cls).toMatch(/\bw-full\b/);
    expect(cls).toMatch(/md:w-\[28rem\]/);
    expect(cls).toMatch(/h-dvh/);
  });
});
