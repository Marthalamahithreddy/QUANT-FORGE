import { AppLink } from "@/components/ui/app-link";
import { Suspense } from "react";

import { BenchmarkSelector } from "@/components/research/benchmark-selector";
import { Button } from "@/components/ui/button";
import { SelectField, TextField } from "@/components/ui/fields";
import { EPS_KINDS, PERIOD_CLASSES, CIK_PATTERN } from "@/lib/api/schema";
import { EPS_KIND_LABEL, PERIOD_CLASS_LABEL } from "@/lib/filings-view";
import { DEFAULT_HORIZON_TEXT, RESEARCH_EVENTS_PATH, researchHref, type ResearchConfigState } from "@/lib/research-view";

const ERROR_ID = "research-config-errors";
const PRESETS: readonly (readonly number[])[] = [[1], [1, 5], [1, 5, 20, 60]];

export interface ResearchConfigFormProps {
  readonly eventId: string;
  readonly config: ResearchConfigState;
  /** The company's CIK from the event record: only used to link to the filing list, never to choose a filing. */
  readonly companyCik: string | null;
}

const invalid = (config: ResearchConfigState, ...params: string[]) => config.errors.some((e) => params.includes(e.param));

/**
 * The study's configuration as a plain GET form: every control is an API parameter, Apply puts the
 * whole configuration in the URL (shareable, reloadable, back/forward-safe), and there is no
 * client-only research state. The benchmark list streams in its own boundary, so a slow or failed
 * benchmark read never blocks or breaks the rest of the page.
 */
export function ResearchConfigForm({ eventId, config, companyCik }: ResearchConfigFormProps) {
  const preset = (horizons: readonly number[]) => researchHref(eventId, { ...config.query, horizons });
  return (
    <form
      id="research-config"
      method="get"
      action={`${RESEARCH_EVENTS_PATH}/${eventId}`}
      aria-label="Research configuration"
      className="scroll-mt-14 flex flex-col gap-4 rounded border border-border bg-surface p-3"
    >
      {/* Applying keeps the benchmark list on the page the reader is browsing, so a benchmark chosen from it is still found by name. */}
      {config.benchmarkPage > 1 ? <input type="hidden" name="bpage" value={config.benchmarkPage} /> : null}
      <div className="flex flex-wrap items-start gap-x-6 gap-y-4">
        <Suspense
          fallback={
            <div className="flex min-w-64 flex-col gap-1" role="status">
              <span className="type-label">Benchmark</span>
              {config.query.benchmarkId ? <input type="hidden" name="benchmark_id" value={config.query.benchmarkId} /> : null}
              <p className="text-sm text-text-2">Loading benchmarks…</p>
            </div>
          }
        >
          <BenchmarkSelector eventId={eventId} config={config} />
        </Suspense>

        <div className="flex flex-col gap-1.5">
          <TextField
            id="horizons"
            label="Horizons (sessions after the anchor)"
            defaultValue={config.shown.horizons}
            maxLength={64}
            mono
            width="w-44"
            invalid={invalid(config, "horizons")}
            describedBy={ERROR_ID}
          />
          <p className="type-meta max-w-xs">
            Comma-separated, 1 to 252, at most 10, no spaces. Empty means the default {DEFAULT_HORIZON_TEXT}.
          </p>
          <p className="type-meta flex flex-wrap gap-x-3">
            <AppLink href={researchHref(eventId, { ...config.query, horizons: undefined })} className="text-accent hover:underline">
              Default ({DEFAULT_HORIZON_TEXT})
            </AppLink>
            {PRESETS.map((horizons) => (
              // A flex item is a block-level target: at least 24 by 24 CSS px (WCAG 2.5.8).
              <AppLink key={horizons.join(",")} href={preset(horizons)} className="inline-flex min-h-6 min-w-6 items-center justify-center text-accent hover:underline">
                {horizons.join(",")}
              </AppLink>
            ))}
          </p>
        </div>

        <fieldset className="flex flex-col gap-2">
          <legend className="type-label mb-1">Filing (optional)</legend>
          <div className="flex flex-wrap items-end gap-3">
            <TextField
              id="filing_accession"
              label="Accession"
              defaultValue={config.shown.filing_accession}
              maxLength={20}
              mono
              width="w-48"
              invalid={invalid(config, "filing_accession")}
              describedBy={ERROR_ID}
            />
            <SelectField
              id="eps_period_class"
              label="Period class"
              defaultValue={config.shown.eps_period_class}
              invalid={invalid(config, "eps_period_class")}
              describedBy={ERROR_ID}
            >
              <option value="">Select…</option>
              {PERIOD_CLASSES.map((value) => (
                <option key={value} value={value}>
                  {PERIOD_CLASS_LABEL[value]}
                </option>
              ))}
            </SelectField>
            <SelectField id="eps_kind" label="EPS kind" defaultValue={config.shown.eps_kind} invalid={invalid(config, "eps_kind")} describedBy={ERROR_ID}>
              <option value="">Select…</option>
              {EPS_KINDS.map((value) => (
                <option key={value} value={value}>
                  {EPS_KIND_LABEL[value]}
                </option>
              ))}
            </SelectField>
          </div>
          <p className="type-meta max-w-md">
            No filing is linked unless you supply an accession. A filing needs both a period class and an EPS kind; neither is assumed.
            {companyCik !== null && CIK_PATTERN.test(companyCik) ? (
              <>
                {" "}
                <AppLink href={`/filings?cik=${companyCik}`} className="text-accent hover:underline">
                  Filings for this registrant (CIK {companyCik})
                </AppLink>{" "}
                lists stored filings with SEC FSDS data. It is a lookup aid: it does not say a filing belongs to this event.
              </>
            ) : null}
          </p>
        </fieldset>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary">
          Apply
        </Button>
        <AppLink href={`${RESEARCH_EVENTS_PATH}/${eventId}`} className="text-sm text-accent hover:underline max-lg:flex max-lg:min-h-11 max-lg:items-center">
          Reset
        </AppLink>
      </div>
    </form>
  );
}
