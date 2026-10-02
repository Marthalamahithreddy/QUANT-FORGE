import { AppShell } from "@/components/layout/app-shell";
import { BackendStatusBadge } from "@/components/layout/topbar";
import { getBackendStatus } from "@/lib/api/health";

const MILESTONE = {
  title: "Foundation milestone",
  summary:
    "Platform scaffolding only. No market data, signals or backtests exist yet.",
  complete: [
    "FastAPI service with /health and /api/v1/health",
    "Pydantic settings, lazy SQLAlchemy engine, empty declarative base",
    "This research-terminal shell and its typed API client",
  ],
  pending: [
    "Price, fundamental, filing and earnings ingestion",
    "EPS-surprise factors and event studies",
    "Backtests, abnormal returns and benchmarks",
  ],
} as const;

export default async function Home() {
  // Resolved once per request on the server. Never polled, never faked.
  const status = await getBackendStatus();

  return (
    <AppShell status={status}>
      <div className="mx-auto flex max-w-4xl flex-col gap-8">
        <header className="flex flex-col gap-3">
          <p className="font-mono text-xs tracking-[0.14em] text-text-muted uppercase">
            Research Terminal
          </p>
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">
            QuantForge
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-text-secondary">
            Event-driven equity research and quantitative backtesting.
          </p>
        </header>

        <section aria-labelledby="milestone-heading" className="flex flex-col gap-3">
          <h2
            id="milestone-heading"
            className="text-xs font-medium tracking-[0.14em] text-text-muted uppercase"
          >
            {MILESTONE.title}
          </h2>
          <p className="max-w-2xl text-sm leading-relaxed text-text-secondary">
            {MILESTONE.summary}
          </p>
        </section>

        <section aria-labelledby="backend-heading" className="flex flex-col gap-3">
          <h2
            id="backend-heading"
            className="text-xs font-medium tracking-[0.14em] text-text-muted uppercase"
          >
            Backend connectivity
          </h2>
          <BackendStatusBadge status={status} variant="block" />
        </section>

        <section className="grid gap-4 sm:grid-cols-2">
          <div className="rounded-md border border-border-subtle bg-surface-1 p-4">
            <h3 className="text-sm font-medium text-text-primary">In place</h3>
            <ul className="mt-3 flex flex-col gap-2">
              {MILESTONE.complete.map((item) => (
                <li
                  key={item}
                  className="flex gap-2 text-sm leading-relaxed text-text-secondary"
                >
                  <span aria-hidden="true" className="mt-2 size-1 shrink-0 rounded-full bg-status-ok" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-md border border-border-subtle bg-surface-1 p-4">
            <h3 className="text-sm font-medium text-text-primary">Not yet built</h3>
            <ul className="mt-3 flex flex-col gap-2">
              {MILESTONE.pending.map((item) => (
                <li
                  key={item}
                  className="flex gap-2 text-sm leading-relaxed text-text-secondary"
                >
                  <span
                    aria-hidden="true"
                    className="mt-2 size-1 shrink-0 rounded-full bg-text-muted"
                  />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
