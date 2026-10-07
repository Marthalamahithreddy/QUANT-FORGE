/**
 * Research workspace: URL state.
 *
 * The URL is the whole configuration of a study, so it is shareable, refreshable and
 * back/forward-safe. Parameter names are the API's own (`horizons`, `benchmark_id`,
 * `filing_accession`, `eps_period_class`, `eps_kind`) so an address can be pasted into an API call.
 *
 * Parsing is strict and local: a malformed value, a repeated parameter, an out-of-range horizon or
 * a half-specified filing is reported BEFORE any request, and nothing is coerced or repaired. The
 * canonical address omits defaults (the default horizons, no benchmark, no filing, page 1) and
 * writes horizons ascending; a valid but non-canonical address is redirected to it.
 *
 * Nothing here chooses a benchmark or a filing. Absent means "none".
 */

import type { ObservationQuery } from "@/lib/api/research";
import {
  ACCESSION_PATTERN,
  DEFAULT_HORIZONS,
  EPS_KINDS,
  HORIZON_MAX_COUNT,
  HORIZON_MAX_VALUE,
  PAGE_LIMIT_MAX,
  PERIOD_CLASSES,
  type EpsKind,
  type EpsPeriodClass,
} from "@/lib/api/schema";
import { isCanonical, readParam, type ListError, type RawSearchParams } from "@/lib/list-query";

export const RESEARCH_EVENTS_PATH = "/research/events";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HORIZON_TOKEN = /^[1-9]\d{0,2}$/;
const BENCHMARK_PAGE = /^[1-9]\d{0,3}$/;

/** The benchmark selector shows the index a page at a time; the first page is not the universe. */
export const BENCHMARK_PAGE_SIZE = PAGE_LIMIT_MAX;

export const DEFAULT_HORIZON_TEXT = DEFAULT_HORIZONS.join(",");

export interface ResearchConfigState {
  /** What the reader typed (for the form), valid or not. */
  readonly shown: {
    readonly horizons: string;
    readonly benchmark_id: string;
    readonly filing_accession: string;
    readonly eps_period_class: string;
    readonly eps_kind: string;
  };
  /** The request, built only from VALID values; absent fields mean "not requested". */
  readonly query: ObservationQuery;
  /** The horizons the study will use (the request's ascending, or the default). */
  readonly horizons: readonly number[];
  readonly horizonsAreDefault: boolean;
  /** 1-based page of the benchmark selector. UI position only: it is not part of the study. */
  readonly benchmarkPage: number;
  readonly errors: readonly ListError[];
  /** Canonical address of this view; `null` when the URL is invalid. */
  readonly canonical: string | null;
  /** The study's identity (canonical address without the selector position): keys the result region. */
  readonly studyKey: string | null;
  readonly redirect: boolean;
}

function parseHorizons(text: string, repeated: boolean): { horizons: number[] | null; error: string | null } {
  const message = `Horizons must be comma-separated whole numbers of sessions from 1 to ${HORIZON_MAX_VALUE}, at most ${HORIZON_MAX_COUNT}, no spaces and no duplicates (for example ${DEFAULT_HORIZON_TEXT}).`;
  if (repeated) return { horizons: null, error: "Horizons must be a single value." };
  if (text === "") return { horizons: null, error: null };
  const tokens = text.split(",");
  if (tokens.length > HORIZON_MAX_COUNT || !tokens.every((token) => HORIZON_TOKEN.test(token))) return { horizons: null, error: message };
  const values = tokens.map((token) => Number.parseInt(token, 10));
  if (values.some((value) => value > HORIZON_MAX_VALUE)) return { horizons: null, error: message };
  if (new Set(values).size !== values.length) return { horizons: null, error: "Horizons must not repeat a value." };
  return { horizons: values.sort((a, b) => a - b), error: null };
}

const sameAsDefault = (horizons: readonly number[]) =>
  horizons.length === DEFAULT_HORIZONS.length && horizons.every((value, index) => value === DEFAULT_HORIZONS[index]);

/** The address of a study. Defaults are omitted; commas stay readable. */
export function researchHref(eventId: string, query: ObservationQuery = {}, benchmarkPage = 1): string {
  const parts: string[] = [];
  const add = (key: string, value: string) => parts.push(`${key}=${encodeURIComponent(value).replace(/%2C/g, ",")}`);
  const horizons = query.horizons && query.horizons.length > 0 ? [...query.horizons].sort((a, b) => a - b) : null;
  if (horizons && !sameAsDefault(horizons)) add("horizons", horizons.join(","));
  if (query.benchmarkId) add("benchmark_id", query.benchmarkId.toLowerCase());
  if (query.filingAccession) {
    add("filing_accession", query.filingAccession);
    if (query.epsPeriodClass) add("eps_period_class", query.epsPeriodClass);
    if (query.epsKind) add("eps_kind", query.epsKind);
  }
  if (benchmarkPage > 1) add("bpage", String(benchmarkPage));
  return `${RESEARCH_EVENTS_PATH}/${eventId}${parts.length > 0 ? `?${parts.join("&")}` : ""}`;
}

export function parseResearchConfig(eventId: string, raw: RawSearchParams): ResearchConfigState {
  const horizonsParam = readParam(raw, "horizons");
  const benchmarkParam = readParam(raw, "benchmark_id");
  const accessionParam = readParam(raw, "filing_accession");
  const periodParam = readParam(raw, "eps_period_class");
  const kindParam = readParam(raw, "eps_kind");
  const bpageParam = readParam(raw, "bpage");
  const errors: ListError[] = [];

  const { horizons: parsedHorizons, error: horizonError } = parseHorizons(horizonsParam.text, horizonsParam.repeated);
  if (horizonError) errors.push({ param: "horizons", message: horizonError });

  let benchmarkId: string | undefined;
  if (benchmarkParam.repeated || (benchmarkParam.text !== "" && !UUID.test(benchmarkParam.text))) {
    errors.push({ param: "benchmark_id", message: "Benchmark must be a UUID, as a single value. Pick one from the list instead of typing it." });
  } else if (benchmarkParam.text !== "") {
    benchmarkId = benchmarkParam.text.toLowerCase();
  }

  let filingAccession: string | undefined;
  if (accessionParam.repeated || (accessionParam.text !== "" && !ACCESSION_PATTERN.test(accessionParam.text))) {
    errors.push({ param: "filing_accession", message: "Filing accession must look like 0000000000-00-000000 (ten digits, two digits, six digits), as a single value." });
  } else if (accessionParam.text !== "") {
    filingAccession = accessionParam.text;
  }

  let period: EpsPeriodClass | undefined;
  if (periodParam.repeated || (periodParam.text !== "" && !(PERIOD_CLASSES as readonly string[]).includes(periodParam.text))) {
    errors.push({ param: "eps_period_class", message: `Period class must be ${PERIOD_CLASSES.join(" or ")}, as a single value.` });
  } else if (periodParam.text !== "") {
    period = periodParam.text as EpsPeriodClass;
  }
  let kind: EpsKind | undefined;
  if (kindParam.repeated || (kindParam.text !== "" && !(EPS_KINDS as readonly string[]).includes(kindParam.text))) {
    errors.push({ param: "eps_kind", message: `EPS kind must be ${EPS_KINDS.join(" or ")}, as a single value.` });
  } else if (kindParam.text !== "") {
    kind = kindParam.text as EpsKind;
  }
  // A filing is only ever evaluated with BOTH selectors, chosen by the reader: never inferred.
  if (filingAccession !== undefined && !errors.some((e) => e.param === "eps_period_class" || e.param === "eps_kind") && (period === undefined || kind === undefined)) {
    errors.push({
      param: period === undefined ? "eps_period_class" : "eps_kind",
      message: "A filing needs both a period class and an EPS kind. Neither is assumed, even from the form type.",
    });
  }

  let benchmarkPage = 1;
  if (bpageParam.present && !(bpageParam.text === "" && !bpageParam.repeated)) {
    if (bpageParam.repeated || !BENCHMARK_PAGE.test(bpageParam.text)) errors.push({ param: "bpage", message: "Benchmark page must be a whole number from 1, as a single value." });
    else benchmarkPage = Number.parseInt(bpageParam.text, 10);
  }

  const horizons = parsedHorizons ?? DEFAULT_HORIZONS;
  const valid = errors.length === 0;
  const query: ObservationQuery = {
    horizons: valid && parsedHorizons && !sameAsDefault(parsedHorizons) ? parsedHorizons : undefined,
    benchmarkId: valid ? benchmarkId : undefined,
    // Selectors without a filing have no effect; they are dropped, never sent (the API rejects them).
    filingAccession: valid ? filingAccession : undefined,
    epsPeriodClass: valid && filingAccession ? period : undefined,
    epsKind: valid && filingAccession ? kind : undefined,
  };
  const canonical = valid ? researchHref(eventId, query, benchmarkPage) : null;
  const studyKey = valid ? researchHref(eventId, query, 1) : null;

  return {
    shown: {
      horizons: horizonsParam.text,
      benchmark_id: benchmarkParam.text,
      filing_accession: accessionParam.text,
      eps_period_class: periodParam.text,
      eps_kind: kindParam.text,
    },
    query,
    horizons,
    horizonsAreDefault: sameAsDefault(horizons),
    benchmarkPage,
    errors,
    canonical,
    studyKey,
    redirect: canonical !== null && !isCanonical(raw, canonical),
  };
}
