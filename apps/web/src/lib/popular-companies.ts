/**
 * The companies featured on the Companies page.
 *
 * A fixed, curated list of IDENTITIES only (a CIK and the ticker it is known by). It is not a ranking
 * and not live data: QuantForge has no market-popularity, price-movement, trading-volume or sentiment
 * source, so the list is not "trending" and its order (alphabetical by ticker) carries no meaning.
 * Every name and figure shown for an entry is read from the API; an entry that is not stored there is
 * shown as unavailable, never filled in.
 */

export interface PopularCompany {
  readonly cik: string;
  readonly ticker: string;
}

export const POPULAR_COMPANIES: readonly PopularCompany[] = [
  { cik: "0000320193", ticker: "AAPL" },
  { cik: "0000002488", ticker: "AMD" },
  { cik: "0001018724", ticker: "AMZN" },
  { cik: "0001652044", ticker: "GOOGL" },
  { cik: "0001326801", ticker: "META" },
  { cik: "0000789019", ticker: "MSFT" },
  { cik: "0000320187", ticker: "NKE" },
  { cik: "0001045810", ticker: "NVDA" },
  { cik: "0001321655", ticker: "PLTR" },
  { cik: "0001318605", ticker: "TSLA" },
];
