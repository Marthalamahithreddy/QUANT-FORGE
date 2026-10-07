/**
 * Presentation of the verifier's changed paths.
 *
 * The paths are the BACKEND's (dotted, with list indexes: `evidence.stock.bars[1].close`). This
 * module only groups them for reading, by their leading segments, preserving the backend's order
 * inside a group. It never compares, diffs or interprets a value: it has none.
 */

export interface PathGroup {
  /** The shared leading segments, for example `evidence.stock` or `descriptive.study`. */
  readonly area: string;
  readonly paths: readonly string[];
}

/** Segments of a dotted path with list indexes kept on their segment: `a.b[1].c` -> `a`, `b[1]`, `c`. */
const segments = (path: string): readonly string[] => path.split(".");

/**
 * Groups by the first segment, except `evidence` (the manifest's widest section), which is grouped
 * one level deeper so `evidence.stock` and `evidence.benchmark` read as separate areas.
 */
export function areaOf(path: string): string {
  const [head, second] = segments(path);
  if (head === "evidence" && second !== undefined) return `${head}.${second.replace(/\[\d+\]$/, "")}`;
  return (head ?? path).replace(/\[\d+\]$/, "");
}

export function groupPaths(paths: readonly string[]): readonly PathGroup[] {
  const groups = new Map<string, string[]>();
  for (const path of paths) {
    const area = areaOf(path);
    const list = groups.get(area);
    if (list) list.push(path);
    else groups.set(area, [path]);
  }
  return [...groups].map(([area, list]) => ({ area, paths: list }));
}
