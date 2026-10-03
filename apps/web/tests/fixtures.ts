import type { EarningsEvent } from "@/types/earnings";

/** Test-only builder. Production code never contains sample data. */
export function makeEvent(overrides: Partial<EarningsEvent> = {}): EarningsEvent {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    company: {
      id: "22222222-2222-4222-8222-222222222222",
      display_name: "Acme Corp",
      legal_name: "Acme Corporation",
      cik: "0000012345",
    },
    source: { name: "test-vendor", provider_type: "MARKET_DATA_VENDOR" },
    listings: [
      {
        ticker: "ACME",
        mic: "XNAS",
        exchange_name: "Nasdaq",
        security_name: "Common",
        security_type: "EQUITY",
        valid_from: "2010-01-01",
        valid_to: null,
        is_primary: true,
        valid_on_event_date: true,
      },
    ],
    fiscal_year: 2024,
    fiscal_quarter: 1,
    event_date: "2024-03-15",
    event_timestamp: null,
    timing: "BEFORE_MARKET",
    currency: "USD",
    reported_eps: "1.1000",
    estimated_eps: "1.0000",
    reported_revenue: null,
    estimated_revenue: null,
    eps_surprise: {
      status: "AVAILABLE",
      absolute: "0.1000",
      ratio: "0.1",
      reason: "computed from reported and estimated EPS",
    },
    availability: {
      observed_at: null,
      observed_at_basis: null,
      pit_eligibility: "INELIGIBLE_NO_AVAILABILITY_EVIDENCE",
      pit_reason: "The source did not supply a historical availability time (observed_at is not set).",
    },
    ingested_at: "2024-03-16T00:00:00Z",
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
