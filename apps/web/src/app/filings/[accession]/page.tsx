import Link from "next/link";
import type { ReactNode } from "react";

import {
  ComparativeEpsPanel,
  CurrentEpsPanel,
  EpsChangePanel,
  EvidenceDetails,
  RequestSummary,
} from "@/components/filings/eps-panels";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { StatePanel } from "@/components/ui/state-panel";
import { getFilingEps } from "@/lib/api/filings";
import { getBackendStatus } from "@/lib/api/health";
import {
  BOUNDARY_LINES,
  EPS_KIND_LABEL,
  PERIOD_CLASS_LABEL,
  isAccession,
  isCikValue,
  isEpsKind,
  isPeriodClass,
  readSelector,
  type Selector,
} from "@/lib/filings-view";
import { MISSING } from "@/lib/format";
import { EPS_KINDS, PERIOD_CLASSES } from "@/types/filings";

export const metadata = { title: "Filing EPS · QuantForge" };

const SELECT =
  "rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-sm text-text-primary";

function Boundary() {
  return (
    <aside
      role="note"
      aria-label="Data boundary"
      className="rounded-md border border-border-strong bg-surface-2 px-4 py-3 text-sm text-text-primary"
    >
      {BOUNDARY_LINES.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </aside>
  );
}

function SelectorField<T extends string>({
  id,
  label,
  options,
  labels,
  selector,
}: {
  readonly id: string;
  readonly label: string;
  readonly options: readonly T[];
  readonly labels: Record<T, string>;
  readonly selector: Selector<T>;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-text-muted">
        {label}
      </label>
      <select
        id={id}
        name={id}
        required
        // A missing or unsupported value is never pre-selected: the user must choose.
        defaultValue={selector.state === "ok" ? selector.value : ""}
        className={SELECT}
      >
        {selector.state === "ok" ? null : (
          <option value="" disabled>
            Select…
          </option>
        )}
        {options.map((option) => (
          <option key={option} value={option}>
            {labels[option]}
          </option>
        ))}
      </select>
    </div>
  );
}

function Controls({
  accession,
  cik,
  period,
  kind,
}: {
  readonly accession: string;
  /** The validated registrant qualifier, carried through the form so a change never drops it. */
  readonly cik: string | null;
  readonly period: Selector<(typeof PERIOD_CLASSES)[number]>;
  readonly kind: Selector<(typeof EPS_KINDS)[number]>;
}) {
  return (
    <form
      method="get"
      action={`/filings/${encodeURIComponent(accession)}`}
      aria-label="EPS selection"
      className="flex flex-wrap items-end gap-3"
    >
      {cik === null ? null : <input type="hidden" name="cik" value={cik} />}
      <SelectorField
        id="period_class"
        label="Period class"
        options={PERIOD_CLASSES}
        labels={PERIOD_CLASS_LABEL}
        selector={period}
      />
      <SelectorField
        id="eps_kind"
        label="EPS kind"
        options={EPS_KINDS}
        labels={EPS_KIND_LABEL}
        selector={kind}
      />
      <button
        type="submit"
        className="rounded-md border border-border-strong bg-surface-3 px-3 py-1.5 text-sm text-text-primary hover:border-accent"
      >
        Show EPS
      </button>
    </form>
  );
}

function describeSelectorProblem(
  period: Selector<string>,
  kind: Selector<string>,
): { invalid: string | null; missing: string[] } {
  const invalid: string[] = [];
  const missing: string[] = [];
  if (period.state === "invalid") invalid.push(`period_class “${period.raw}”`);
  if (kind.state === "invalid") invalid.push(`eps_kind “${kind.raw}”`);
  if (period.state === "missing") missing.push("period class");
  if (kind.state === "missing") missing.push("EPS kind");
  return {
    invalid:
      invalid.length > 0 ? `Unsupported ${invalid.join(" and ")}.` : null,
    missing,
  };
}

export default async function FilingDetailPage({
  params,
  searchParams,
}: PageProps<"/filings/[accession]">) {
  const { accession } = await params;
  const query = await searchParams;
  const period = readSelector(query.period_class, isPeriodClass);
  const kind = readSelector(query.eps_kind, isEpsKind);
  // Optional registrant qualifier: absent is fine, malformed is a local error, never repaired.
  // An empty `cik=` is malformed (the API answers 422), not "absent": it must not silently become
  // an accession-only request.
  const cik: Selector<string> =
    query.cik === ""
      ? { state: "invalid", raw: "" }
      : readSelector(query.cik, isCikValue);
  const registrant = cik.state === "ok" ? cik.value : null;
  const wellFormed = isAccession(accession);
  const problem = describeSelectorProblem(period, kind);

  const canRequest =
    wellFormed &&
    cik.state !== "invalid" &&
    period.state === "ok" &&
    kind.state === "ok";
  const [status, result] = await Promise.all([
    getBackendStatus(),
    canRequest
      ? getFilingEps(accession, period.value, kind.value, registrant)
      : Promise.resolve(null),
  ]);

  let body: ReactNode;
  if (!wellFormed) {
    body = (
      <StatePanel kind="error" title="Malformed accession">
        An accession looks like 0000320193-24-000123 (ten digits, two digits,
        six digits). No request was made.
      </StatePanel>
    );
  } else if (cik.state === "invalid") {
    // The registrant qualifier is part of the filing's identity and has no control here, so the
    // controls are withheld: re-submitting them would silently drop it and ask for the accession
    // alone (ambiguous, or another registrant's filing).
    body = (
      <StatePanel kind="error" title="Unsupported registrant CIK">
        The CIK in the address (“{cik.raw}”) must be exactly ten digits,
        including leading zeros. Nothing is substituted, and no request was
        made. Correct the address, or open the filing again from the{" "}
        <Link href="/filings" className="text-accent hover:underline">
          filings list
        </Link>
        .
      </StatePanel>
    );
  } else if (problem.invalid) {
    body = (
      <StatePanel kind="error" title="Unsupported parameter">
        {problem.invalid} Choose a supported value below. Nothing is
        substituted, and no request was made.
      </StatePanel>
    );
  } else if (result === null) {
    body = (
      <StatePanel kind="empty" title="Choose what to show">
        Select the {problem.missing.join(" and ")} to load. The page never
        guesses it, even from the form type.
      </StatePanel>
    );
  } else if (result.state === "unavailable") {
    body = (
      <StatePanel kind="error" title="The database is unavailable">
        The QuantForge API could not read this filing. This is a service
        problem, not a statement about the filing. Try again shortly.
      </StatePanel>
    );
  } else if (result.state === "contract") {
    body = (
      <StatePanel kind="error" title="The API returned an unsupported response">
        {result.detail}
      </StatePanel>
    );
  } else if (result.state === "rejected") {
    body = (
      <StatePanel kind="error" title="The API rejected this request">
        {result.detail}
      </StatePanel>
    );
  } else if (result.state === "error") {
    body = (
      <StatePanel kind="error" title="This filing could not be loaded">
        {result.detail}
      </StatePanel>
    );
  } else {
    const eps = result.data;
    body = (
      <>
        <RequestSummary eps={eps} />
        <CurrentEpsPanel current={eps.current} />
        <ComparativeEpsPanel comparative={eps.comparative} />
        <EpsChangePanel change={eps.change} />
        <EvidenceDetails eps={eps} />
      </>
    );
  }

  const header = result?.state === "ok" ? result.data : null;

  return (
    <AppShell status={status}>
      <div className="mx-auto flex max-w-4xl flex-col gap-5">
        <Link href="/filings" className="text-sm text-accent hover:underline">
          ← Filings
        </Link>
        <PageHeader
          eyebrow="Filing · SEC FSDS"
          title={accession}
          description={
            header && header.form === null && registrant !== null ? (
              // A qualified refusal that matched no stored filing: the CIK is only the request
              // echoed back, never evidence that a registrant was found.
              <>
                Requested CIK {registrant} · no stored filing matched (request
                echo, not a stored registrant)
              </>
            ) : header ? (
              <>
                {header.form ?? MISSING} · CIK {header.company_cik ?? MISSING}
              </>
            ) : registrant !== null ? (
              <>Requested CIK {registrant}</>
            ) : undefined
          }
        />
        <Boundary />
        {wellFormed && cik.state !== "invalid" ? (
          <Controls
            accession={accession}
            cik={registrant}
            period={period}
            kind={kind}
          />
        ) : null}
        {wellFormed && cik.state !== "invalid" ? (
          <p className="-mt-3 text-xs text-text-muted">
            The selections above come from the address. Links from the filings
            list pre-select the period class from the form type and start with
            Diluted, as a convenience only; change either choice and press Show
            EPS. The page never falls back from one EPS kind to another. A CIK
            in the address names which registrant&rsquo;s filing is meant (an
            identity, not an availability time); without one the accession alone
            is used, which can be ambiguous for a joint accession.
          </p>
        ) : null}
        {body}
      </div>
    </AppShell>
  );
}
