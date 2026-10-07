import type { Metadata } from "next";
import { AppLink } from "@/components/ui/app-link";
import type { ReactNode } from "react";

import {
  ComparativeEpsPanel,
  CurrentEpsPanel,
  EpsChangePanel,
  EvidenceDetails,
  RequestSummary,
} from "@/components/filings/eps-panels";
import { ApiFailureState } from "@/components/ui/api-failure";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { SelectField } from "@/components/ui/fields";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { getFilingEps } from "@/lib/api/filings";
import { EPS_KINDS, PERIOD_CLASSES } from "@/lib/api/schema";
import {
  BOUNDARY_LINES,
  filingViewHref,
  EPS_KIND_LABEL,
  PERIOD_CLASS_LABEL,
  isAccession,
  isCik,
  isEpsKind,
  isPeriodClass,
  readSelector,
  type Selector,
} from "@/lib/filings-view";
import { MISSING } from "@/lib/format";

export async function generateMetadata({ params }: PageProps<"/filings/[accession]">): Promise<Metadata> {
  return { title: `${(await params).accession} · Filing` };
}

const ERROR_ID = "filing-selector-errors";

function Boundary() {
  return (
    <Callout role="note" label="Data boundary">
      {BOUNDARY_LINES.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </Callout>
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
    <SelectField
      id={id}
      label={label}
      required
      // A missing or unsupported value is never pre-selected: the user must choose.
      defaultValue={selector.state === "ok" ? selector.value : ""}
      invalid={selector.state === "invalid"}
      describedBy={ERROR_ID}
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
    </SelectField>
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
      <SelectorField id="period_class" label="Period class" options={PERIOD_CLASSES} labels={PERIOD_CLASS_LABEL} selector={period} />
      <SelectorField id="eps_kind" label="EPS kind" options={EPS_KINDS} labels={EPS_KIND_LABEL} selector={kind} />
      <Button type="submit">Show EPS</Button>
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
    invalid: invalid.length > 0 ? `Unsupported ${invalid.join(" and ")}.` : null,
    missing,
  };
}

export default async function FilingDetailPage({ params, searchParams }: PageProps<"/filings/[accession]">) {
  const { accession } = await params;
  const query = await searchParams;
  const period = readSelector(query.period_class, isPeriodClass);
  const kind = readSelector(query.eps_kind, isEpsKind);
  // Optional registrant qualifier: absent is fine, malformed is a local error, never repaired.
  // An empty `cik=` is malformed (the API answers 422), not "absent": it must not silently become
  // an accession-only request.
  const cik: Selector<string> =
    query.cik === "" ? { state: "invalid", raw: "" } : readSelector(query.cik, isCik);
  const registrant = cik.state === "ok" ? cik.value : null;
  const wellFormed = isAccession(accession);
  const problem = describeSelectorProblem(period, kind);

  const selection =
    wellFormed && cik.state !== "invalid" && period.state === "ok" && kind.state === "ok"
      ? { period: period.value, kind: kind.value }
      : null;
  // Exactly one API request, and none unless every selector is valid.
  const result = selection ? await getFilingEps(accession, selection.period, selection.kind, registrant) : null;

  let body: ReactNode;
  if (!wellFormed) {
    body = (
      <ErrorState title="Malformed accession">
        An accession looks like 0000000000-00-000000 (ten digits, two digits, six digits). No request was made.
      </ErrorState>
    );
  } else if (cik.state === "invalid") {
    // The registrant qualifier is part of the filing's identity and has no control here, so the
    // controls are withheld: re-submitting them would silently drop it and ask for the accession
    // alone (ambiguous, or another registrant's filing).
    body = (
      <ErrorState title="Unsupported registrant CIK">
        The CIK in the address (“{cik.raw}”) must be exactly ten digits, including leading zeros. Nothing is
        substituted, and no request was made. Correct the address, or open the filing again from the{" "}
        <AppLink href="/filings" className="text-accent hover:underline">
          filings list
        </AppLink>
        .
      </ErrorState>
    );
  } else if (problem.invalid) {
    body = (
      <ErrorState id={ERROR_ID} title="Unsupported parameter">
        {problem.invalid} Choose a supported value above. Nothing is substituted, and no request was made.
      </ErrorState>
    );
  } else if (result === null) {
    body = (
      <EmptyState title="Choose what to show">
        Select the {problem.missing.join(" and ")} to load. The page never guesses it, even from the form type.
      </EmptyState>
    );
  } else if (result.kind !== "ok") {
    body = (
      <ApiFailureState
        failure={result}
        subject="this filing"
        retryHref={selection ? filingViewHref(accession, { cik: registrant, ...selection }) : undefined}
      />
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

  const header = result?.kind === "ok" ? result.data : null;

  return (
    <PageContainer>
      <Breadcrumbs items={[{ label: "Filings", href: "/filings" }, { label: accession }]} />
      <PageHeader
        eyebrow="Filing · SEC FSDS"
        title={accession}
        description={
          header && header.form === null && registrant !== null ? (
            // A qualified refusal that matched no stored filing: the CIK is only the request
            // echoed back, never evidence that a registrant was found.
            <>Requested CIK {registrant} · no stored filing matched (request echo, not a stored registrant)</>
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
        <Controls accession={accession} cik={registrant} period={period} kind={kind} />
      ) : null}
      {wellFormed && cik.state !== "invalid" ? (
        <p className="type-meta -mt-3 max-w-3xl">
          The selections above come from the address. Links from the filings list pre-select the period class from
          the form type and start with Diluted, as a convenience only; change either choice and press Show EPS. The
          page never falls back from one EPS kind to another. A CIK in the address names which registrant&rsquo;s
          filing is meant (an identity, not an availability time); without one the accession alone is used, which can
          be ambiguous for a joint accession.
        </p>
      ) : null}
      {body}
    </PageContainer>
  );
}
