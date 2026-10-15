import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { FilingEvidence } from "@/components/companies/evidence";
import { HistoryTable } from "@/components/companies/history-table";
import { MetricCards } from "@/components/companies/metric-cards";
import { QuarterlyTrend } from "@/components/companies/trend";
import { ApiFailureState } from "@/components/ui/api-failure";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Callout } from "@/components/ui/callout";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { Section } from "@/components/ui/section";
import { getCompanyFinancials } from "@/lib/api/companies";
import { COMPANIES_PATH, companyHref, fiscalLabel } from "@/lib/companies-view";
import { isCik } from "@/lib/filings-view";

export async function generateMetadata({ params }: PageProps<"/companies/[cik]">): Promise<Metadata> {
  return { title: `${(await params).cik} · Company` };
}

export default async function CompanyPage({ params }: PageProps<"/companies/[cik]">) {
  const { cik } = await params;
  if (!isCik(cik)) {
    return (
      <PageContainer>
        <Breadcrumbs items={[{ label: "Companies", href: COMPANIES_PATH }, { label: cik }]} />
        <ErrorState title="Malformed CIK">A CIK is exactly ten digits, including leading zeros. No request was made.</ErrorState>
      </PageContainer>
    );
  }
  const result = await getCompanyFinancials(cik);
  if (result.kind === "not_found") notFound();
  if (result.kind !== "ok") {
    return (
      <PageContainer>
        <Breadcrumbs items={[{ label: "Companies", href: COMPANIES_PATH }, { label: cik }]} />
        <ApiFailureState failure={result} subject="this company's financials" retryHref={companyHref(cik)} />
      </PageContainer>
    );
  }

  const { company, filings, boundary_note: boundary } = result.data;
  const latest = filings[0];
  return (
    <PageContainer width="workspace">
      <Breadcrumbs items={[{ label: "Companies", href: COMPANIES_PATH }, { label: company.name }]} />
      <PageHeader
        eyebrow="Company · SEC financial facts"
        title={company.name}
        description={
          <>
            {company.tickers.length > 0 ? `${company.tickers.join(", ")} · ` : ""}CIK {company.cik}
          </>
        }
      />
      <Callout role="note" label="Data boundary">
        <p>{boundary}</p>
      </Callout>

      {latest === undefined ? (
        <EmptyState title="No SEC financial data is stored for this company">
          Revenue, net income and diluted EPS appear once a quarterly or annual filing&rsquo;s financial facts have been ingested. Nothing is
          estimated or filled in, and opening this page fetched nothing.
        </EmptyState>
      ) : (
        <>
          <Section
            title={`Most recent stored filing: ${fiscalLabel(latest)} · ${latest.form}`}
            note="Newest reporting period among the stored filings."
          >
            <MetricCards filing={latest} />
          </Section>
          {filings.some((filing) => filing.period_class === "QUARTER") ? (
            <Section
              title="Quarterly trend"
              note="Stored 10-Q quarters, oldest first. Bar length is the magnitude of the exact figure."
            >
              <QuarterlyTrend filings={filings} />
              <p className="type-meta max-w-3xl">
                A fiscal fourth quarter is reported in the 10-K, not a 10-Q, and is never derived by subtraction, so it is not
                plotted here. Annual figures are in the comparison table below.
              </p>
            </Section>
          ) : null}
          <Section
            title="Historical comparison"
            note={`${filings.length} stored ${filings.length === 1 ? "filing" : "filings"} with financial facts`}
          >
            <HistoryTable filings={filings} />
            <p className="type-meta max-w-3xl">
              {filings.length === 1
                ? "Only one filing's financial facts are stored for this company, so the history is a single period beside its prior-year comparative. A multi-period trend appears as more filings are ingested; nothing is interpolated."
                : "Each row is one stored filing with the prior-year figure as presented in that same filing. Periods that are not stored are not filled in or interpolated."}
            </p>
          </Section>
          {filings.map((filing) => (
            <FilingEvidence key={filing.accession} filing={filing} cik={company.cik} />
          ))}
        </>
      )}
    </PageContainer>
  );
}
