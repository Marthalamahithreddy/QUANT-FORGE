import { useId, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import {
  CHANGE_STATUS_LABEL,
  COMPARATIVE_STATUS_LABEL,
  EPS_KIND_LABEL,
  EPS_STATUS_LABEL,
  PERCENTAGE_REASON_DETAIL,
  PERIOD_CLASS_LABEL,
  TRANSITION_LABEL,
  statusTone,
} from "@/lib/filings-view";
import {
  MISSING,
  formatDate,
  formatDecimal,
  formatRatioAsPercent,
  formatSignedDecimal,
} from "@/lib/format";
import type {
  ComparativeEps,
  CurrentEps,
  EpsChange,
  FactEvidence,
  FilingEps,
} from "@/types/filings";

function Section({
  title,
  note,
  children,
}: {
  readonly title: string;
  readonly note?: string;
  readonly children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={headingId}
      className="rounded-md border border-border-subtle bg-surface-1"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-border-subtle px-4 py-2">
        <h2
          id={headingId}
          className="text-[11px] font-medium tracking-[0.14em] text-text-muted uppercase"
        >
          {title}
        </h2>
        {note ? <p className="text-xs text-text-muted">{note}</p> : null}
      </div>
      <dl className="grid gap-x-6 gap-y-3 p-4 sm:grid-cols-[11rem_minmax(0,1fr)]">
        {children}
      </dl>
    </section>
  );
}

function Row({
  label,
  children,
}: {
  readonly label: string;
  readonly children: ReactNode;
}) {
  return (
    <>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="font-tabular min-w-0 text-sm break-words text-text-primary">
        {children}
      </dd>
    </>
  );
}

const muted = (text: string) => <span className="text-text-muted">{text}</span>;

/** Status as text (code and readable label); the badge tone is only a secondary cue. */
function StatusRow({
  status,
  label,
  reason,
}: {
  readonly status: string;
  readonly label: string;
  readonly reason: string;
}) {
  return (
    <>
      <Row label="Status">
        <Badge tone={statusTone(status)}>{label}</Badge>{" "}
        <span aria-hidden="true" className="font-mono text-xs text-text-muted">
          {status}
        </span>
      </Row>
      <Row label="Reason">
        <span className="text-text-secondary">{reason}</span>
      </Row>
    </>
  );
}

/** A fact value: exact string, or an explicit statement that the source gave none (never 0). */
function FactValue({ fact }: { readonly fact: FactEvidence }) {
  return fact.value === null ? (
    muted("No value stated by the source (null, not zero)")
  ) : (
    <span className="font-mono">{formatDecimal(fact.value)}</span>
  );
}

function FactRows({ fact }: { readonly fact: FactEvidence }) {
  return (
    <>
      <Row label="Value">
        <FactValue fact={fact} />
      </Row>
      <Row label="Unit">{fact.unit}</Row>
      <Row label="Period end">{formatDate(fact.period_end)}</Row>
    </>
  );
}

export function CurrentEpsPanel({ current }: { readonly current: CurrentEps }) {
  return (
    <Section title="Current EPS">
      <StatusRow
        status={current.status}
        label={EPS_STATUS_LABEL[current.status]}
        reason={current.reason}
      />
      {current.fact ? <FactRows fact={current.fact} /> : null}
    </Section>
  );
}

export function ComparativeEpsPanel({
  comparative,
}: {
  readonly comparative: ComparativeEps;
}) {
  return (
    <Section title="Comparative EPS" note="As presented in this filing">
      <StatusRow
        status={comparative.status}
        label={COMPARATIVE_STATUS_LABEL[comparative.status]}
        reason={comparative.reason}
      />
      {comparative.fact ? <FactRows fact={comparative.fact} /> : null}
      {comparative.status === "COMPARATIVE_NULL_VALUE" ? (
        <Row label="Source evidence">
          Matching source evidence exists, but its value is null. It is not
          treated as zero.
        </Row>
      ) : null}
    </Section>
  );
}

export function EpsChangePanel({ change }: { readonly change: EpsChange }) {
  return (
    <Section
      title="EPS change"
      note="Current minus comparative, computed by the API"
    >
      <Row label="Status">
        <Badge tone={statusTone(change.status)}>
          {CHANGE_STATUS_LABEL[change.status]}
        </Badge>{" "}
        <span aria-hidden="true" className="font-mono text-xs text-text-muted">
          {change.status}
        </span>
      </Row>
      {change.status === "INPUT_UNAVAILABLE" || change.eps_change === null ? (
        <Row label="EPS change">
          {muted(
            "Not computed: the current or comparative EPS is unavailable (see their statuses above). No value is shown.",
          )}
        </Row>
      ) : (
        <>
          <Row label="EPS change">
            <span className="font-mono">
              {formatSignedDecimal(change.eps_change)}
            </span>
          </Row>
          <Row label="Transition">
            {change.transition === null
              ? MISSING
              : TRANSITION_LABEL[change.transition]}
            {change.transition === null ? null : (
              <>
                {" "}
                <span
                  aria-hidden="true"
                  className="font-mono text-xs text-text-muted"
                >
                  {change.transition}
                </span>
              </>
            )}
          </Row>
          <Row label="Percentage change">
            {change.percentage_change === null ? (
              <>
                {muted(
                  "Percentage change is not defined for this comparative base.",
                )}
                {change.percentage_unavailable_reason === null ? null : (
                  <div className="text-xs text-text-muted">
                    {
                      PERCENTAGE_REASON_DETAIL[
                        change.percentage_unavailable_reason
                      ]
                    }
                  </div>
                )}
              </>
            ) : (
              <PercentageValue ratio={change.percentage_change} />
            )}
          </Row>
        </>
      )}
    </Section>
  );
}

/** The ratio as a rounded percentage; a non-zero ratio that rounds to zero says so, never "0%". */
function PercentageValue({ ratio }: { readonly ratio: string }) {
  const text = formatRatioAsPercent(ratio);
  const hiddenByRounding = !/^[+-]/.test(text) && /[1-9]/.test(ratio);
  return (
    <>
      <span className="font-mono">{text}</span>
      {hiddenByRounding ? (
        <div className="text-xs text-text-muted">
          Not zero: the ratio is too small to show at this precision. See the
          exact ratio under Evidence and exact values.
        </div>
      ) : null}
    </>
  );
}

function FactEvidenceBlock({
  title,
  fact,
}: {
  readonly title: string;
  readonly fact: FactEvidence;
}) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-xs font-medium text-text-secondary">{title}</h3>
      <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-[11rem_minmax(0,1fr)]">
        <Row label="Fact id">
          <span className="font-mono">{fact.fact_id}</span>
        </Row>
        <Row label="Tag">
          <span className="font-mono">{fact.tag}</span>
        </Row>
        <Row label="Taxonomy version">
          <span className="font-mono">{fact.taxonomy_version}</span>
        </Row>
        <Row label="Unit">{fact.unit}</Row>
        <Row label="Period end">{formatDate(fact.period_end)}</Row>
        <Row label="Quarters (qtrs)">{fact.qtrs}</Row>
        <Row label="Segments">
          {fact.segments === "" ? (
            muted("(empty, as stored)")
          ) : (
            <code>{fact.segments}</code>
          )}
        </Row>
        <Row label="Co-registrant">
          {fact.coreg === "" ? (
            muted("(empty, as stored)")
          ) : (
            <code>{fact.coreg}</code>
          )}
        </Row>
        <Row label="Exact value">
          {fact.value === null ? muted("null") : <code>{fact.value}</code>}
        </Row>
      </dl>
    </div>
  );
}

/** Selected facts and exact strings as the API sent them; no availability metadata is invented. */
export function EvidenceDetails({ eps }: { readonly eps: FilingEps }) {
  const { current, comparative, change } = eps;
  return (
    <details className="rounded-md border border-border-subtle bg-surface-1">
      <summary className="cursor-pointer rounded-md px-4 py-2 text-sm font-medium text-text-primary focus-visible:outline-2 focus-visible:outline-accent">
        Evidence and exact values
      </summary>
      <div className="flex flex-col gap-5 border-t border-border-subtle p-4">
        <p className="text-xs text-text-muted">
          Exactly what the API returned for the selected facts. It carries no
          availability or acceptance time.
        </p>
        {current.fact ? (
          <FactEvidenceBlock title="Current EPS fact" fact={current.fact} />
        ) : null}
        {comparative.fact ? (
          <FactEvidenceBlock
            title="Comparative EPS fact"
            fact={comparative.fact}
          />
        ) : null}
        {change.eps_change !== null ? (
          <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-[11rem_minmax(0,1fr)]">
            <Row label="Exact EPS change">
              <code>{change.eps_change}</code>
            </Row>
            <Row label="Exact ratio">
              {change.percentage_change === null ? (
                muted("not defined")
              ) : (
                <code>{change.percentage_change}</code>
              )}
            </Row>
          </dl>
        ) : null}
        {current.fact === null &&
        comparative.fact === null &&
        change.eps_change === null ? (
          <p className="text-sm text-text-secondary">
            No fact was selected for this request.
          </p>
        ) : null}
      </div>
    </details>
  );
}

export function RequestSummary({ eps }: { readonly eps: FilingEps }) {
  return (
    <p className="text-xs text-text-muted">
      Showing {PERIOD_CLASS_LABEL[eps.period_class].toLowerCase()} EPS ·{" "}
      {EPS_KIND_LABEL[eps.eps_kind].toLowerCase()}. No fallback to another EPS
      concept is made.
    </p>
  );
}
