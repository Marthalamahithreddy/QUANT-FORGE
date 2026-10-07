import { expect, test } from "./support";

const LIGHT_BG = "rgb(243, 245, 248)";
const DARK_BG = "rgb(10, 14, 19)";

test.describe("Theme", () => {
  test("a chosen theme survives client navigation, reloads and new pages", async ({ page }) => {
    await page.goto("/");
    const html = page.locator("html");
    const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);

    // No cookie yet: the theme is System, so there is no explicit attribute, and the OS (dark) decides.
    await expect(html).not.toHaveAttribute("data-theme", /.+/);
    expect(await background()).toBe(DARK_BG);
    await page.getByRole("radio", { name: "Light" }).check({ force: true });
    await expect(html).toHaveAttribute("data-theme", "light");
    expect(await background()).toBe(LIGHT_BG);

    // Client navigation: the shell stays mounted and the theme with it.
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Earnings" }).click();
    await expect(page).toHaveURL("/earnings");
    await expect(html).toHaveAttribute("data-theme", "light");
    expect(await background()).toBe(LIGHT_BG);

    // A full load is rendered with the right theme from the server (no flash): the cookie carries it.
    await page.reload();
    await expect(html).toHaveAttribute("data-theme", "light");
    expect(await page.context().cookies()).toEqual(expect.arrayContaining([expect.objectContaining({ name: "qf-theme", value: "light" })]));

    await page.goto("/research/saved");
    await expect(html).toHaveAttribute("data-theme", "light");
    expect(await background()).toBe(LIGHT_BG);

    // And back: dark is a full peer, and System defers to the operating system.
    await page.getByRole("radio", { name: "Dark" }).check({ force: true });
    expect(await background()).toBe(DARK_BG);
    await page.getByRole("radio", { name: "System" }).check({ force: true });
    await expect(html).not.toHaveAttribute("data-theme", /.+/);
  });

  test("the OS preference decides while the theme is System", async ({ page, context }) => {
    await context.clearCookies();
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(LIGHT_BG);
  });
});
