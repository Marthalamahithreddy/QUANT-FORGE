import { expect, settled, test } from "./support";

test("the page declares an icon that is served, and no document load ends in a 404", async ({ page }) => {
  const notFound: string[] = [];
  page.on("response", (response) => {
    if (response.status() === 404) notFound.push(new URL(response.url()).pathname);
  });
  await page.goto("/");
  await settled(page);

  const href = await page.locator('link[rel~="icon"]').first().getAttribute("href");
  expect(href, "no <link rel=icon>").toBeTruthy();
  const icon = await page.request.get(new URL(href!, page.url()).toString());
  expect(icon.status()).toBe(200);
  expect(icon.headers()["content-type"]).toContain("image/svg+xml");

  // Chrome asks for /favicon.ico only when no icon is declared; it must not 404 here.
  await page.waitForTimeout(500);
  expect(notFound).toEqual([]);
});
