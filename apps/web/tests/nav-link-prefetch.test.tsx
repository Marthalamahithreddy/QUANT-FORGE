import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const linkProps: Record<string, unknown>[] = [];

vi.mock("next/link", () => ({
  default: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
    linkProps.push(props);
    const { children, href, ...rest } = props;
    delete rest.prefetch; // not a DOM attribute
    return (
      <a href={String(href)} {...(rest as object)}>
        {children}
      </a>
    );
  },
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/earnings" }));

import { AppLink } from "@/components/ui/app-link";
import { NavLink } from "@/components/layout/nav-link";
import { NAV_ITEMS } from "@/lib/navigation";

describe("the sidebar follows the frozen no-prefetch policy", () => {
  it("every navigation link renders with prefetch disabled", () => {
    linkProps.length = 0;
    for (const item of NAV_ITEMS) render(<NavLink item={item} />);
    expect(linkProps).toHaveLength(NAV_ITEMS.length);
    for (const props of linkProps) expect(props.prefetch).toBe(false);
  });

  it("keeps the current-section marker", () => {
    const earnings = NAV_ITEMS.find((item) => item.href === "/earnings")!;
    render(<NavLink item={earnings} />);
    expect(screen.getByRole("link", { name: earnings.label })).toHaveAttribute("aria-current", "page");
  });

  it("a caller cannot switch prefetch back on", () => {
    linkProps.length = 0;
    // @ts-expect-error `prefetch` is not part of AppLink's props
    render(<AppLink href="/x" prefetch>
      x
    </AppLink>);
    expect(linkProps[0]!.prefetch).toBe(false);
  });
});
