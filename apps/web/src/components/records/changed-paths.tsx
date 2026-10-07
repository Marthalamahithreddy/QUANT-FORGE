import { groupPaths } from "@/lib/changed-paths";

/** A group with more paths than this starts collapsed (its count is in the summary). */
const OPEN_LIMIT = 10;

/**
 * The verifier's changed paths, grouped by the area they sit in and shown exactly as supplied. A
 * path is plain text that wraps (a long dotted path is never cut and never widens the page); no
 * value is shown, compared or diffed here, because the API supplies paths only.
 */
export function ChangedPaths({ title, paths, explanation }: { readonly title: string; readonly paths: readonly string[]; readonly explanation: string }) {
  const groups = groupPaths(paths);
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div>
        <h3 className="text-xs font-medium tracking-wide text-text-2 uppercase">
          {title} ({paths.length})
        </h3>
        <p className="type-meta max-w-3xl">{explanation}</p>
      </div>
      {groups.length === 0 ? (
        <p className="type-meta">None.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {groups.map((group) => (
            <details key={group.area} open={group.paths.length <= OPEN_LIMIT} className="min-w-0 rounded border border-border-subtle">
              <summary className="cursor-pointer px-2 py-1.5 text-sm max-lg:min-h-11">
                <span className="type-id">{group.area}</span> <span className="type-meta">· {group.paths.length}</span>
              </summary>
              <ul className="flex min-w-0 flex-col gap-1 border-t border-border-subtle px-2 py-1.5">
                {group.paths.map((path) => (
                  <li key={path} className="min-w-0">
                    <code className="type-id break-all">{path}</code>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
