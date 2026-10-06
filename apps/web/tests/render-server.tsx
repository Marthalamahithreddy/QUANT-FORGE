import { render } from "@testing-library/react";
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";

type AsyncComponent = (props: unknown) => Promise<ReactNode>;

const isAsync = (type: unknown): type is AsyncComponent =>
  typeof type === "function" && type.constructor.name === "AsyncFunction";

/**
 * Resolves the async SERVER components in a tree (the page's streamed data regions) the way the
 * server would, leaving ordinary components alone. React's client renderer cannot await them, and
 * pages stream their results behind `Suspense`, so tests expand them here and render the result.
 */
export async function expandServer(node: ReactNode): Promise<ReactNode> {
  if (Array.isArray(node)) return Promise.all(node.map(expandServer));
  if (!isValidElement(node)) return node;

  const element = node as ReactElement<{ children?: ReactNode }>;
  if (isAsync(element.type)) {
    return expandServer(await element.type(element.props));
  }
  const { children } = element.props;
  if (children === undefined) return element;
  const expanded = Array.isArray(children)
    ? await Promise.all(children.map(expandServer))
    : await expandServer(children);
  return Array.isArray(expanded) ? cloneElement(element, undefined, ...expanded) : cloneElement(element, undefined, expanded);
}

/** Renders a page (the value its async function resolved to), streamed regions included. */
export async function renderServer(page: ReactNode) {
  return render(<>{await expandServer(page)}</>);
}
