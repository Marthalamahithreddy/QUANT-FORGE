import { describe, expect, it } from "vitest";

import { RECORD_PAGE_SIZE } from "@/lib/api/schema";
import { groupPaths, areaOf } from "@/lib/changed-paths";
import { pinsTheWorkspaceCannotCarry, offsetOfRecordPage, parseRecordView, parseSavedList, recordHref, rerunHref, rerunQuery, savedPageHref } from "@/lib/record-view";
import { parseResearchConfig } from "@/lib/research-view";
import { recordDetail } from "./records-fixtures";

const ID = "3f2888da-3d82-49c2-8767-67fae4c802f1";

describe("the saved list address", () => {
  it("has one canonical form: page 1 is the bare path, and the page size is fixed (no limit parameter)", () => {
    expect(parseSavedList({})).toMatchObject({ page: 1, canonical: "/research/saved", redirect: false, errors: [] });
    expect(savedPageHref(1)).toBe("/research/saved");
    expect(savedPageHref(3)).toBe("/research/saved?page=3");
    expect(parseSavedList({ page: "3" })).toMatchObject({ page: 3, canonical: "/research/saved?page=3", redirect: false });
    expect(offsetOfRecordPage(1)).toBe(0);
    expect(offsetOfRecordPage(3)).toBe(2 * RECORD_PAGE_SIZE);
  });

  it("redirects page=1, an empty page and unknown parameters to the canonical form", () => {
    expect(parseSavedList({ page: "1" })).toMatchObject({ canonical: "/research/saved", redirect: true });
    expect(parseSavedList({ page: "" })).toMatchObject({ canonical: "/research/saved", redirect: true });
    expect(parseSavedList({ limit: "5", ticker: "X" })).toMatchObject({ canonical: "/research/saved", redirect: true });
  });

  it("rejects an invalid page before any request and invents no filters", () => {
    for (const page of ["0", "-1", "1.5", "abc", "10001", ["1", "2"]]) {
      const state = parseSavedList({ page });
      expect(state.errors, String(page)).toHaveLength(1);
      expect(state.canonical).toBeNull();
    }
  });
});

describe("the record address and the explicit verify request", () => {
  it("shows the stored baseline only by default and verifies only on ?verify=1", () => {
    expect(parseRecordView(ID, {})).toMatchObject({ verify: false, canonical: `/research/saved/${ID}`, redirect: false, errors: [] });
    expect(parseRecordView(ID, { verify: "1" })).toMatchObject({ verify: true, canonical: `/research/saved/${ID}?verify=1`, redirect: false });
    expect(recordHref(ID)).toBe(`/research/saved/${ID}`);
    expect(recordHref(ID, true)).toBe(`/research/saved/${ID}?verify=1`);
  });

  it("drops an empty verify and unknown parameters, and rejects any other value without verifying", () => {
    expect(parseRecordView(ID, { verify: "" })).toMatchObject({ verify: false, redirect: true, canonical: `/research/saved/${ID}` });
    expect(parseRecordView(ID, { x: "1" })).toMatchObject({ verify: false, redirect: true });
    for (const verify of ["0", "true", "yes", ["1", "1"]]) {
      const state = parseRecordView(ID, { verify });
      expect(state.verify, String(verify)).toBe(false);
      expect(state.errors).toHaveLength(1);
      expect(state.canonical).toBeNull();
    }
  });
});

describe("the re-run live address", () => {
  it("carries only parameters the workspace accepts, in the workspace's own canonical form", () => {
    const detail = recordDetail("complete_filing_accounting");
    const href = rerunHref(detail.request);
    expect(href).toBe(
      `/research/events/${detail.request.event_id}?horizons=1,2&benchmark_id=${detail.request.benchmark_id}&filing_accession=0000320193-24-000010&eps_period_class=QUARTER&eps_kind=DILUTED`,
    );
    for (const stored of [detail.record_id, detail.manifest_fingerprint, detail.result_fingerprint, detail.manifest.evidence.event.fingerprint]) {
      expect(href).not.toContain(stored);
    }
    expect(href).not.toMatch(/record|fingerprint|verify|security|source/);
  });

  it("omits the default horizons and everything not requested", () => {
    const detail = recordDetail("complete_default_horizons");
    expect(rerunHref(detail.request)).toBe(`/research/events/${detail.request.event_id}`);
    const bare = recordDetail("complete_plain");
    expect(rerunHref(bare.request)).toBe(`/research/events/${bare.request.event_id}?horizons=1,2`);
  });

  it("is accepted unchanged by the workspace's own parser (it is already canonical, so it never redirects)", () => {
    for (const name of ["complete_filing_accounting", "complete_benchmark", "complete_plain", "complete_default_horizons"]) {
      const detail = recordDetail(name);
      const href = rerunHref(detail.request);
      const url = new URL(href, "http://x");
      const state = parseResearchConfig(detail.request.event_id, Object.fromEntries(url.searchParams));
      expect(state.errors, name).toEqual([]);
      expect(state.redirect, name).toBe(false);
      expect(state.canonical, name).toBe(href);
    }
  });

  it("drops selectors that have no filing, as the workspace would", () => {
    expect(rerunQuery({ ...recordDetail("complete_plain").request, eps_kind: "DILUTED", eps_period_class: "QUARTER" })).toMatchObject({ epsKind: undefined, epsPeriodClass: undefined });
  });

  it("says when the stored request pinned something the workspace cannot carry", () => {
    expect(pinsTheWorkspaceCannotCarry({ security_id: null, stock_source_id: null, benchmark_source_id: null })).toEqual([]);
    expect(pinsTheWorkspaceCannotCarry({ security_id: "x", stock_source_id: "y", benchmark_source_id: null })).toEqual(["a security", "a stock price source"]);
  });
});

describe("changed-path grouping (presentation only)", () => {
  it("groups by the leading area, keeping the backend's order and the paths unchanged", () => {
    const paths = ["evidence.stock.bars[1].close", "descriptive.study.stock_returns.forward_returns[0].return_value", "evidence.stock.bars[1].ingested_at", "evidence.benchmark.bars[0].close", "event.reported_eps"];
    const groups = groupPaths(paths);
    expect(groups.map((g) => g.area)).toEqual(["evidence.stock", "descriptive", "evidence.benchmark", "event"]);
    expect(groups.flatMap((g) => g.paths).sort()).toEqual([...paths].sort());
    expect(groups[0]!.paths).toEqual(["evidence.stock.bars[1].close", "evidence.stock.bars[1].ingested_at"]);
    expect(areaOf("evidence.accounting.facts[0].value")).toBe("evidence.accounting");
    expect(groupPaths([])).toEqual([]);
  });
});
