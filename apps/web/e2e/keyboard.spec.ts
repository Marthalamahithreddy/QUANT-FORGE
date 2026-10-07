import type { Page } from "@playwright/test";

import { DATA, expect, mode, settled, test } from "./support";

/** What the focused element must be. Evaluated inside the page, so it is data, never a closure. */
interface Target {
  readonly href?: string;
  readonly id?: string;
  /** The element's accessible label (aria-label, else its text) starts with this. */
  readonly label?: string;
  readonly inNav?: boolean;
}

/**
 * Presses Tab until the focused element matches `target`, failing if it is never reached.
 * Keyboard-only: nothing in these journeys is clicked.
 */
async function tabTo(page: Page, target: Target, limit = 120) {
  for (let step = 0; step < limit; step += 1) {
    await page.keyboard.press("Tab");
    const hit = await page.evaluate((want) => {
      const active = document.activeElement;
      if (active === null || active === document.body) return false;
      const text = (active.getAttribute("aria-label") ?? active.textContent ?? "").trim();
      return (
        (want.href === undefined || active.getAttribute("href") === want.href) &&
        (want.id === undefined || active.id === want.id) &&
        (want.label === undefined || text.startsWith(want.label)) &&
        (want.inNav !== true || active.closest("nav") !== null)
      );
    }, target);
    if (hit) return;
  }
  throw new Error(`Tab never reached ${JSON.stringify(target)}`);
}

test.describe("Keyboard-only journeys", () => {
  test("primary navigation, Earnings to an event, and into Research", async ({ page }) => {
    await page.goto("/");
    await tabTo(page, { href: "/earnings", inNav: true });
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL("/earnings");

    // The skip link is the first stop of a page and moves past the shell.
    await page.reload();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#main-content$/);

    await tabTo(page, { href: `/earnings/${DATA.event}` });
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`/earnings/${DATA.event}`);

    await tabTo(page, { label: "Open Research for" });
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`/research/events/${DATA.event}`);
  });

  test("Research configuration, including the benchmark selector, by keyboard", async ({ page }) => {
    await page.goto(`/research/events/${DATA.event}`);
    await settled(page);
    const form = page.getByRole("form", { name: "Research configuration" });

    await tabTo(page, { id: "benchmark_id" });
    await expect(form.getByLabel("Benchmark")).toBeFocused();
    // Nothing is selected until the reader moves: the first option is "None".
    await expect(form.getByLabel("Benchmark")).toHaveValue("");
    // Type-ahead selects an option on a closed select in every browser, without opening its popup.
    await page.keyboard.type(`Observation Index (OBSI) · USD · id ${DATA.benchmark.slice(0, 3)}`);
    await expect(form.getByLabel("Benchmark")).toHaveValue(DATA.benchmark);

    await tabTo(page, { id: "horizons" });
    await page.keyboard.type("1,2");
    await page.keyboard.press("Enter");

    await expect.poll(() => [...new URL(page.url()).searchParams.keys()].sort()).toEqual(["benchmark_id", "horizons"]);
    await settled(page);
    await expect(mode(page, "DESCRIPTIVE")).toContainText("+7.50%");
  });

  test("an evidence drawer opens with focus inside, and Escape returns focus to its button", async ({ page }) => {
    await page.goto(`/research/events/${DATA.event}`);
    await settled(page);
    await tabTo(page, { label: "Evidence" });
    const trigger = page.locator(":focus");
    await page.keyboard.press("Enter");

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    expect(await page.evaluate(() => !!document.activeElement?.closest("dialog"))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test("Saved research, verification and Re-run live, by keyboard", async ({ page, fixture }) => {
    const record = DATA.record("complete_benchmark");
    await page.goto("/research/saved");
    await tabTo(page, { href: `/research/saved/${record.id}` });
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`/research/saved/${record.id}`);
    await settled(page);
    expect(await fixture.served("/verify")).toHaveLength(0);

    await tabTo(page, { label: "Verify against current evidence" });
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`/research/saved/${record.id}?verify=1#verification`);
    await expect(page.locator('[data-verification="MATCH"]')).toBeVisible();
    // The verification is in view: the address fragment scrolled to it.
    const top = await page.evaluate(() => document.querySelector("#verification")!.getBoundingClientRect().top);
    expect(top).toBeGreaterThanOrEqual(0);
    expect(top).toBeLessThan(900);

    await tabTo(page, { label: "Re-run this request live" });
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/research/events/${record.eventId}\\?`));
  });
});
