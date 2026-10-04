import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { Frame } from "@/components/ui/frame";
import { PageContainer } from "@/components/ui/page-container";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { StatePanel } from "@/components/ui/state-panel";
import { getEarningsEvent } from "@/lib/api/earnings";
import { getBackendStatus } from "@/lib/api/health";
import {
  BASIS_LABEL,
  BASIS_MEANING,
  PIT_PRESENTATION,
  TIMING_LABEL,
} from "@/lib/availability";
import {
  MISSING,
  formatDate,
  formatDecimal,
  formatRatioAsPercent,
  formatSignedDecimal,
  formatUtcInstant,
} from "@/lib/format";

export const metadata = { title: "Earnings event" };

function Section({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <Frame title={title}>
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-[11rem_minmax(0,1fr)]">{children}</dl>
    </Frame>
  );
}

function Row({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num min-w-0 text-sm break-words text-text-primary">{children}</dd>
    </>
  );
}

const muted = (text: string) => <span className="text-text-muted">{text}</span>;

export default async function EarningsDetailPage({ params }: PageProps<"/earnings/[id]">) {
  const { id } = await params;
  const [status, result] = await Promise.all([getBackendStatus(), getEarningsEvent(id)]);

  if (result.state === "not_found") {
    notFound();
  }

  return (
    <AppShell
      status={status}
      breadcrumbs={[
        { label: "Earnings", href: "/earnings" },
        {
          label:
            result.state === "ok"
              ? `${result.data.company.display_name} · ${result.data.event_date}`
              : "Event",
        },
      ]}
    >
      <PageContainer>
        {result.state === "error" ? (
          <StatePanel kind="error" title="This event could not be loaded">
            {result.detail}
          </StatePanel>
        ) : (
          <EventDetail event={result.data} />
        )}
      </PageContainer>
    </AppShell>
  );
}

function EventDetail({
  event,
}: {
  readonly event: Extract<Awaited<ReturnType<typeof getEarningsEvent>>, { state: "ok" }>["data"];
}) {
  const { availability: a, eps_surprise: s } = event;
  const pit = PIT_PRESENTATION[a.pit_eligibility];
  const period =
    event.fiscal_quarter === null
      ? `FY${event.fiscal_year}`
      : `FY${event.fiscal_year} Q${event.fiscal_quarter}`;

  return (
    <>
      <PageHeader
        eyebrow={`Earnings observation · ${period}`}
        title={event.company.display_name}
        description={`${event.event_date} · ${TIMING_LABEL[event.timing]}`}
        actions={<Badge tone={pit.tone}>{pit.label}</Badge>}
      />

      <Section title="Event">
        <Row label="Company">
          {event.company.display_name}
          {event.company.legal_name !== event.company.display_name ? (
            <span className="text-text-muted"> · {event.company.legal_name}</span>
          ) : null}
        </Row>
        <Row label="CIK">{event.company.cik ?? muted(MISSING)}</Row>
        <Row label="Event date">{formatDate(event.event_date)}</Row>
        <Row label="Event instant">
          {event.event_timestamp === null
            ? muted("Source gave a date only")
            : formatUtcInstant(event.event_timestamp)}
        </Row>
        <Row label="Timing">
          {TIMING_LABEL[event.timing]}
          <span className="text-text-muted"> · as stated by the source</span>
        </Row>
        <Row label="Reported EPS">{formatDecimal(event.reported_eps)}</Row>
        <Row label="Estimated EPS">{formatDecimal(event.estimated_eps)}</Row>
        <Row label="EPS surprise">
          {s.absolute === null ? (
            muted(s.reason)
          ) : (
            <>
              {formatSignedDecimal(s.absolute)}
              <span className="text-text-muted">
                {" "}
                · {s.ratio === null ? "percentage undefined" : formatRatioAsPercent(s.ratio)}
              </span>
            </>
          )}
        </Row>
        <Row label="Currency">{event.currency ?? muted(MISSING)}</Row>
        <Row label="Reported revenue">{formatDecimal(event.reported_revenue)}</Row>
        <Row label="Estimated revenue">{formatDecimal(event.estimated_revenue)}</Row>
      </Section>

      <Section title="Listings of this company">
        {event.listings.length === 0 ? (
          <Row label="Listings">{muted("No listings are stored for this company.")}</Row>
        ) : (
          event.listings.map((l) => (
            <Row key={`${l.mic}-${l.ticker}-${l.valid_from}`} label={l.mic}>
              <span className="font-mono">{l.ticker}</span>
              <span className="text-text-muted">
                {" "}
                · {l.exchange_name} · valid {l.valid_from} to {l.valid_to ?? "present"}
                {l.is_primary ? " · primary" : ""}
              </span>{" "}
              <Badge tone={l.valid_on_event_date ? "positive" : "neutral"}>
                {l.valid_on_event_date ? "Valid on event date" : "Not valid on event date"}
              </Badge>
            </Row>
          ))
        )}
      </Section>

      <Section title="Availability">
        <Row label="Observed at">
          {a.observed_at === null ? (
            <>
              Availability not disclosed
              <div className="text-text-muted">
                The source did not supply a historical publication time.
              </div>
            </>
          ) : (
            formatUtcInstant(a.observed_at)
          )}
        </Row>
        <Row label="Evidence basis">
          {a.observed_at_basis === null ? (
            muted(MISSING)
          ) : (
            <>
              {BASIS_LABEL[a.observed_at_basis]}
              <div className="text-text-muted">{BASIS_MEANING[a.observed_at_basis]}</div>
              {a.pit_eligibility === "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS" ? (
                <div className="text-text-muted">
                  This basis is not currently approved for point-in-time use.
                </div>
              ) : null}
            </>
          )}
        </Row>
        <Row label="Point-in-time">
          <Badge tone={pit.tone}>{pit.label}</Badge>
          <div className="mt-1 text-text-secondary">{a.pit_reason}</div>
        </Row>
      </Section>

      <Section title="Provenance">
        <Row label="Source">{event.source.name}</Row>
        <Row label="Provider type">{event.source.provider_type}</Row>
        <Row label="Ingested at">{formatUtcInstant(event.ingested_at)}</Row>
      </Section>
    </>
  );
}
