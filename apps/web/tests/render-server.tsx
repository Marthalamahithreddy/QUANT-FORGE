import { render } from "@testing-library/react";
import { type ReactNode } from "react";
import { prerender } from "react-dom/static";

/**
 * Applies what React's inline completion scripts would do in a browser: every streamed
 * `Suspense` segment (`<div hidden id="S:n">`) replaces the fallback of its boundary
 * (`<template id="B:n">` ... `<!--/$-->`). Repeated, because a segment can hold further boundaries.
 */
function resolveBoundaries(container: HTMLElement) {
  for (let guard = 0; guard < 200; guard += 1) {
    const segment = container.querySelector<HTMLElement>('div[hidden][id^="S:"]');
    if (!segment) break;
    const placeholder = container.querySelector(`template[id="B:${segment.id.slice(2)}"]`);
    if (!placeholder) {
      segment.remove();
      continue;
    }
    let node = placeholder.nextSibling;
    while (node && !(node.nodeType === Node.COMMENT_NODE && (node as Comment).data === "/$")) {
      const next = node.nextSibling;
      node.remove();
      node = next;
    }
    if (node?.parentNode) {
      while (segment.firstChild) node.parentNode.insertBefore(segment.firstChild, node);
    }
    placeholder.remove();
    segment.remove();
  }
  container.querySelectorAll("script").forEach((script) => script.remove());
}

/**
 * Renders a page the way the server does: React's static renderer resolves async Server
 * Components and `Suspense` boundaries at ANY depth (a sync component may render an async child),
 * and the resolved HTML is placed in the document so Testing Library queries see what a reader
 * would. No hydration happens, so this is for server-rendered structure and content; interactive
 * client components are tested with `render` directly.
 */
export async function renderServer(page: ReactNode) {
  const { prelude } = await prerender(<>{page}</>);
  // Mounted through Testing Library, so its cleanup (also called mid-test) removes it.
  const rendered = render(<div dangerouslySetInnerHTML={{ __html: await new Response(prelude).text() }} />);
  resolveBoundaries(rendered.container);
  return rendered;
}
