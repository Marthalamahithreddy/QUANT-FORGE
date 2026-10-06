import { useId } from "react";

import { StateGlyph, type GlyphKind } from "@/components/state/glyphs";
import { formatRatioAsPercent, ratioRoundsToZero } from "@/lib/format";
import { axisScale, markPosition, slotCentre, tickIndexes, tickLabel, tickPosition } from "@/lib/chart/geometry";
import { benchmarkRequested, horizonRows, type HorizonRow } from "@/lib/research-rows";
import { BENCHMARK_RETURN_LABEL, FORWARD_RETURN_LABEL } from "@/lib/research-wording";
import { FORWARD_RETURN_STATE } from "@/lib/state-map";
import type { Study } from "@/lib/api/schema";

const SLOT = 92;
const LEFT = 58;
const RIGHT = 64;
const TOP = 22;
const PLOT_HEIGHT = 190;
const BOTTOM_MARGIN = 62;

/** The exact string as a percent; a non-zero ratio that rounds to 0.00% says so instead of looking like zero. */
const percentText = (ratio: string): string => `${formatRatioAsPercent(ratio)}${ratioRoundsToZero(ratio) ? " (not zero)" : ""}`;

const glyphOf = (state: string): GlyphKind | null => (state === "VALUE" || state === "NOT_APPLICABLE" ? null : (state as GlyphKind));

/** What a screen reader (and the native tooltip) says for one horizon, from the exact strings. */
function describeRow(row: HorizonRow, requested: boolean): string {
  const stock =
    row.stock.return_value !== null
      ? `stock return ${percentText(row.stock.return_value)}`
      : `stock return ${FORWARD_RETURN_LABEL[row.stock.status].toLowerCase()}`;
  if (!requested) return `+${row.horizon} sessions: ${stock}`;
  const bench =
    row.benchmark.benchmark_return !== null
      ? `benchmark ${percentText(row.benchmark.benchmark_return)}`
      : `benchmark ${BENCHMARK_RETURN_LABEL[row.benchmark.status].toLowerCase()}`;
  const abnormal = row.abnormal.abnormal_return !== null ? `, abnormal return ${percentText(row.abnormal.abnormal_return)}` : "";
  return `+${row.horizon} sessions: ${stock}, ${bench}${abnormal}`;
}

export interface HorizonReturnsChartProps {
  readonly study: Study;
  /** The study's accessible name, e.g. "Descriptive return by horizon". */
  readonly title: string;
}

/**
 * Return by horizon: one measurement per configured horizon, drawn as a categorical dumbbell.
 *
 * - X is categorical and equally spaced (+1, +5, +20 sessions after the anchor session): horizons
 *   are separate measurements from one anchor, so nothing joins them and nothing is interpolated.
 * - Y is the return, symmetric about a labelled zero line.
 * - Stock is a filled circle; the benchmark (only when one was explicitly selected) a hollow
 *   diamond; the segment between them is the backend's abnormal return, drawn between its two
 *   endpoints (its length is never computed here).
 * - A horizon without a stock return has NO mark: an empty slot names its state at the baseline.
 * - The event is never placed on a time axis; the session ladder carries the event's position.
 *
 * The table beside it is authoritative. Every mark is a focusable group whose name carries the
 * exact figures, and every figure shown comes from the exact strings; geometry only places marks
 * (see `lib/chart/geometry.ts`).
 */
export function HorizonReturnsChart({ study, title }: HorizonReturnsChartProps) {
  const rows = horizonRows(study);
  const requested = benchmarkRequested(study);
  const titleId = useId();
  const descId = useId();

  const plotted: string[] = [];
  for (const row of rows) {
    if (row.stock.return_value !== null) plotted.push(row.stock.return_value);
    if (requested && row.benchmark.benchmark_return !== null) plotted.push(row.benchmark.benchmark_return);
  }
  // Nothing to place: the caller shows the refusal instead of an empty coordinate system.
  if (!rows.some((row) => row.stock.return_value !== null)) return null;

  const scale = axisScale(plotted);
  const width = LEFT + RIGHT + SLOT * rows.length;
  const plotTop = TOP;
  const plotBottom = TOP + PLOT_HEIGHT;
  const height = plotBottom + BOTTOM_MARGIN;
  const zeroY = tickPosition(0, scale, plotTop, plotBottom);
  const indexes = tickIndexes(scale);

  return (
    <figure className="flex flex-col gap-2">
      <div role="region" aria-label={`${title}, scrollable`} tabIndex={0} className="relative overflow-x-auto rounded border border-border-subtle">
        <svg
          role="img"
          aria-labelledby={titleId}
          aria-describedby={descId}
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          className="block max-w-none"
        >
          <title id={titleId}>{title}</title>
          <desc id={descId}>
            {`Stock return at ${rows.length} horizon${rows.length === 1 ? "" : "s"}, in sessions after the anchor session${
              requested ? ", with the explicitly selected benchmark and the backend's abnormal return" : ", with no benchmark selected"
            }. Split-adjusted price returns, dividends excluded. The table that follows lists every figure exactly.`}
          </desc>

          {scale.ticks.map((tick, position) => {
            const y = tickPosition(tick, scale, plotTop, plotBottom);
            return (
              <g key={tick}>
                {tick === 0 ? null : <line x1={LEFT} x2={width - RIGHT} y1={y} y2={y} className="stroke-chart-grid" strokeWidth={1} />}
                <text x={LEFT - 8} y={y} textAnchor="end" dominantBaseline="middle" fontSize={11} className="fill-text-3">
                  {tickLabel(scale, indexes[position] ?? 0)}
                </text>
              </g>
            );
          })}
          <line x1={LEFT} x2={width - RIGHT} y1={zeroY} y2={zeroY} className="stroke-chart-zero" strokeWidth={1.5} />
          <text x={12} y={plotTop + PLOT_HEIGHT / 2} fontSize={11} textAnchor="middle" transform={`rotate(-90 12 ${plotTop + PLOT_HEIGHT / 2})`} className="fill-text-3">
            Return (%)
          </text>

          {rows.map((row, index) => {
            const x = slotCentre(index, rows.length, LEFT, width - RIGHT);
            const stockY = row.stock.return_value !== null ? markPosition(row.stock.return_value, scale, plotTop, plotBottom) : null;
            const benchY = requested && row.benchmark.benchmark_return !== null ? markPosition(row.benchmark.benchmark_return, scale, plotTop, plotBottom) : null;
            const stockState = FORWARD_RETURN_STATE[row.stock.status];
            return (
              <g key={row.horizon} tabIndex={0} role="group" aria-label={describeRow(row, requested)} className="outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus">
                <title>{describeRow(row, requested)}</title>
                <rect x={x - SLOT / 2} y={plotTop} width={SLOT} height={PLOT_HEIGHT} fill="transparent" />
                {stockY !== null && benchY !== null ? <line x1={x} x2={x} y1={stockY} y2={benchY} className="stroke-chart-abnormal" strokeWidth={2.5} /> : null}
                {benchY !== null ? (
                  <path d={`M ${x} ${benchY - 6} L ${x + 6} ${benchY} L ${x} ${benchY + 6} L ${x - 6} ${benchY} Z`} fill="none" className="stroke-chart-bench" strokeWidth={2} />
                ) : null}
                {stockY !== null && row.stock.return_value !== null ? (
                  <>
                    <circle cx={x} cy={stockY} r={5} className="fill-chart-stock" />
                    <text x={x + 9} y={stockY} dominantBaseline="middle" fontSize={11} className="num fill-text">
                      {formatRatioAsPercent(row.stock.return_value)}
                    </text>
                  </>
                ) : (
                  <g transform={`translate(${x - SLOT / 2 + 4} ${plotBottom - 34})`} className="fill-text-2">
                    {glyphOf(stockState) ? (
                      <g className="text-text-2">
                        <StateGlyph kind={glyphOf(stockState) as GlyphKind} />
                      </g>
                    ) : null}
                    <text x={16} y={10} fontSize={10.5} className="fill-text-2">
                      {FORWARD_RETURN_LABEL[row.stock.status]}
                    </text>
                  </g>
                )}
                {requested && stockY !== null && benchY === null ? (
                  <text x={x} y={plotBottom - 8} textAnchor="middle" fontSize={10.5} className="fill-text-2">
                    {`Benchmark: ${BENCHMARK_RETURN_LABEL[row.benchmark.status]}`}
                  </text>
                ) : null}
                <text x={x} y={plotBottom + 18} textAnchor="middle" fontSize={12} className="num fill-text">
                  {`+${row.horizon}`}
                </text>
              </g>
            );
          })}
          <text x={(LEFT + width - RIGHT) / 2} y={plotBottom + 42} textAnchor="middle" fontSize={11} className="fill-text-3">
            Sessions after the anchor session
          </text>
        </svg>
      </div>
      <figcaption className="type-meta flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="inline-flex items-center gap-1.5">
          <svg width="12" height="12" aria-hidden="true" focusable="false">
            <circle cx="6" cy="6" r="5" className="fill-chart-stock" />
          </svg>
          Stock return
        </span>
        {requested ? (
          <>
            <span className="inline-flex items-center gap-1.5">
              <svg width="12" height="12" aria-hidden="true" focusable="false">
                <path d="M6 1 L11 6 L6 11 L1 6 Z" fill="none" className="stroke-chart-bench" strokeWidth="1.6" />
              </svg>
              Benchmark return
            </span>
            <span className="inline-flex items-center gap-1.5">
              <svg width="16" height="12" aria-hidden="true" focusable="false">
                <line x1="8" x2="8" y1="1" y2="11" className="stroke-chart-abnormal" strokeWidth="2.5" />
              </svg>
              Abnormal return (the gap between them)
            </span>
          </>
        ) : (
          <span>No benchmark selected: stock returns only.</span>
        )}
        <span>Not to scale in time: each horizon is a separate measurement from the anchor session.</span>
      </figcaption>
    </figure>
  );
}
