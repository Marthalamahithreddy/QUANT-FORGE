import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const ICON = join(__dirname, "..", "src", "app", "icon.svg");

describe("the app icon", () => {
  it("is a static file-convention icon, so no runtime request or icon package is needed", () => {
    expect(existsSync(ICON)).toBe(true);
    const svg = readFileSync(ICON, "utf8");
    expect(svg).toMatch(/^<svg[^>]+xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
    expect(svg).not.toMatch(/<script|<image|href=|@import|url\(/i); // self-contained, no outside request
  });
});
