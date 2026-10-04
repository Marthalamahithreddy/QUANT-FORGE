"use client";

import { useSyncExternalStore } from "react";

import { PREFERENCE_MAX_AGE, THEMES, THEME_COOKIE, parseTheme, type Theme } from "@/lib/preferences";

const LABEL: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

/** The attribute the server rendered is the source of truth; the control only observes it. */
function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}

const clientTheme = (): Theme => parseTheme(document.documentElement.dataset.theme);
/** The server cannot know the theme here, so it claims none (rather than a wrong one). */
const serverTheme = (): Theme | null => null;

function applyTheme(theme: Theme): void {
  // Server-readable cookie (no localStorage): the next request renders the right attribute.
  document.cookie = `${THEME_COOKIE}=${theme}; path=/; max-age=${PREFERENCE_MAX_AGE}; samesite=lax`;
  if (theme === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = theme;
  }
}

/**
 * System / Light / Dark as a native radio group (keyboard arrows, labels and the current state
 * come for free). A Client Component because it writes a cookie and the root attribute on
 * interaction; the selected segment is also styled by CSS from `html[data-theme]`, so it is right
 * on first paint, before hydration.
 */
export function ThemeControl({ name = "qf-theme" }: { readonly name?: string }) {
  const current = useSyncExternalStore(subscribe, clientTheme, serverTheme);
  return (
    <fieldset className="m-0 flex min-w-0 border-0 p-0">
      <legend className="sr-only">Theme</legend>
      <div className="inline-flex">
        {THEMES.map((theme) => (
          <label
            key={theme}
            data-value={theme}
            className="qf-theme-opt relative -ml-px flex cursor-pointer items-center border border-border-control px-2 text-[11px] font-medium text-text-2 first:ml-0 first:rounded-l-md last:rounded-r-md hover:bg-hover max-lg:min-h-11 lg:min-h-7"
          >
            <input
              type="radio"
              name={name}
              value={theme}
              checked={current === theme}
              onChange={() => applyTheme(theme)}
              className="peer absolute inset-0 m-0 cursor-pointer opacity-0"
            />
            <span className="rounded-sm px-0.5 peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus">
              {LABEL[theme]}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
