import type { AbnormalReturn, BenchmarkReturn, ForwardReturn, Study } from "@/lib/api/schema";

/** One horizon across the three lists the API returns (they share an order: the decoder enforces it). */
export interface HorizonRow {
  readonly horizon: number;
  readonly stock: ForwardReturn;
  readonly benchmark: BenchmarkReturn;
  readonly abnormal: AbnormalReturn;
}

export function horizonRows(study: Study): readonly HorizonRow[] {
  return study.stock_returns.forward_returns.map((stock, index) => ({
    horizon: stock.horizon,
    stock,
    // Decoded lists have equal length and horizon order, so position pairs the same horizon.
    benchmark: study.benchmark.returns[index] as BenchmarkReturn,
    abnormal: study.abnormal_returns[index] as AbnormalReturn,
  }));
}

/** A benchmark was explicitly requested for this study. */
export const benchmarkRequested = (study: Study): boolean => study.benchmark.benchmark_id !== null;
