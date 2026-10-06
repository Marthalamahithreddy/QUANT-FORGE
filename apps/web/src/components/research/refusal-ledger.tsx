import { DomainState } from "@/components/state/domain-state";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import type { Refusal, RefusalScope } from "@/lib/api/schema";
import { REFUSAL_SCOPES } from "@/lib/api/schema";
import type { NonValueState } from "@/lib/state";
import { REFUSAL_SCOPE_ANCHOR, REFUSAL_SCOPE_LABEL } from "@/lib/research-wording";
import { REFUSAL_CODE_STATE } from "@/lib/state-map";

const COLUMNS: DataTableColumn<Refusal>[] = [
  { id: "code", header: "Code", cell: (entry) => <span className="type-id whitespace-nowrap">{entry.code}</span> },
  {
    id: "state",
    header: "State",
    cell: (entry) => {
      const state = REFUSAL_CODE_STATE[entry.scope][entry.code];
      // Every code in the ledger is in its scope's vocabulary (the decoder enforces it); a VALUE
      // code cannot be a refusal, so it never reaches a mark.
      return state && state !== "VALUE" ? <DomainState kind={state as NonValueState} /> : null;
    },
  },
  { id: "horizon", header: "Horizon", cell: (entry) => (entry.horizon === null ? <span className="text-text-3">—</span> : <span className="num">+{entry.horizon} sessions</span>) },
  { id: "reason", header: "Reason (from the API)", cell: (entry) => <span className="whitespace-normal text-text-2">{entry.reason}</span> },
];

/**
 * Every part the backend did not compute, grouped by its scope, in the backend's own order. Each
 * entry carries the backend's code and reason verbatim; the mark repeats what kind of statement it
 * is (missing, refused, ambiguous...). A refusal is not an error, and no remedy is invented. The
 * count here equals the count in the result status strip.
 */
export function RefusalLedger({ refusals }: { readonly refusals: readonly Refusal[] }) {
  if (refusals.length === 0) {
    return (
      <p role="status" className="text-sm text-text-2">
        No refusals: every part that was requested was computed.
      </p>
    );
  }
  const groups = REFUSAL_SCOPES.map((scope) => ({ scope, entries: refusals.filter((entry) => entry.scope === scope) })).filter((group) => group.entries.length > 0);
  return (
    <div className="flex flex-col gap-4">
      {groups.map(({ scope, entries }) => (
        <div key={scope} id={`refusals-${scope.toLowerCase()}`} className="scroll-mt-14 flex flex-col gap-1.5">
          <DataTable
            caption={`${REFUSAL_SCOPE_LABEL[scope]} · ${entries.length} refusal${entries.length === 1 ? "" : "s"}`}
            columns={COLUMNS}
            rows={entries}
            rowKey={(entry, index) => `${entry.code}-${entry.horizon ?? "x"}-${index}`}
          />
          <p className="type-meta">
            <a href={`#${REFUSAL_SCOPE_ANCHOR[scope as RefusalScope]}`} className="text-accent hover:underline">
              Where this appears
            </a>
          </p>
        </div>
      ))}
    </div>
  );
}

/** A short pointer from a section to its ledger entries, instead of repeating their paragraphs. */
export function RefusalReference({ scopes, refusals }: { readonly scopes: readonly RefusalScope[]; readonly refusals: readonly Refusal[] }) {
  const matching = refusals.filter((entry) => scopes.includes(entry.scope));
  if (matching.length === 0) return null;
  // Link to a group the ledger really has: the first of these scopes that carries an entry.
  const target = scopes.find((scope) => matching.some((entry) => entry.scope === scope)) ?? scopes[0];
  return (
    <p className="type-meta">
      {matching.length} refusal{matching.length === 1 ? "" : "s"} in this part.{" "}
      <a href={`#refusals-${(target ?? "market").toLowerCase()}`} className="text-accent hover:underline">
        See the refusal ledger
      </a>
      .
    </p>
  );
}
