import { expect, noPageOverflow, test } from "./support";

test.use({ viewport: { width: 390, height: 844 } });

test.describe("Mobile navigation", () => {
  test("opens as a modal drawer, traps focus, closes on Escape and returns focus", async ({ page }) => {
    await page.goto("/earnings");
    const open = page.getByRole("button", { name: "Open navigation" });
    // The desktop rail is not on the page at this width.
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeHidden();

    await open.focus();
    await page.keyboard.press("Enter");
    const drawer = page.getByRole("dialog", { name: "Navigation" });
    await expect(drawer).toBeVisible();
    await expect(page.getByRole("button", { name: "Close navigation" })).toBeFocused();

    // Tab never reaches the page behind the drawer: focus is inside it (or, past its last control, the
    // browser's own chrome, which leaves nothing on the page focused).
    for (let step = 0; step < 12; step += 1) {
      await page.keyboard.press("Tab");
      const where = await page.evaluate(() => {
        const active = document.activeElement;
        return active === null || active === document.body ? "browser" : active.closest("dialog") ? "drawer" : "page";
      });
      expect(where).not.toBe("page");
    }
    // Back inside the drawer for Escape.
    await page.getByRole("button", { name: "Close navigation" }).focus();

    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(open).toBeFocused();
    await noPageOverflow(page);
  });

  test("a link in the drawer navigates and closes it; the theme control is reachable there", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Open navigation" }).click();
    const drawer = page.getByRole("dialog", { name: "Navigation" });
    await expect(drawer.getByRole("radio", { name: "Light" })).toBeAttached();

    const links = await drawer.getByRole("navigation", { name: "Primary menu" }).getByRole("link").allInnerTexts();
    // The product link first, then exactly the five real destinations: no placeholder, no Companies.
    expect(links.slice(-5)).toEqual(["Overview", "Earnings", "Filings", "Research", "Saved research"]);
    expect(links).toHaveLength(6);

    await drawer.getByRole("link", { name: "Saved research" }).click();
    await expect(page).toHaveURL("/research/saved");
    await expect(drawer).toBeHidden();
    await noPageOverflow(page);
  });
});
