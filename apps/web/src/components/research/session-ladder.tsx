import { DomainState } from "@/components/state/domain-state";
import { RefusalNotice } from "@/components/state/refusal-notice";
import { TIMING_LABEL } from "@/lib/availability";
import { formatDate } from "@/lib/format";
import { ALIGNMENT_LABEL, FORWARD_RETURN_LABEL } from "@/lib/research-wording";
import { ALIGNMENT_STATE, FORWARD_RETURN_STATE } from "@/lib/state-map";
import type { NonValueState } from "@/lib/state";
import type { Study } from "@/lib/api/schema";

function Slot({ label, date, note }: { readonly label: string; readonly date: string; readonly note?: string }) {
  return (
    <li className="flex min-w-28 shrink-0 flex-col gap-0.5 rounded border border-border bg-surface px-3 py-2">
      <span className="type-label">{label}</span>
      <span className="num text-sm text-text">{formatDate(date)}</span>
      {note ? <span className="type-meta">{note}</span> : null}
    </li>
  );
}

function EventMarker({ date, timing }: { readonly date: string; readonly timing: string }) {
  return (
    <li data-kind="event-marker" className="flex min-w-32 shrink-0 flex-col justify-center gap-0.5 border-l-2 border-dashed border-text-2 px-3 py-2">
      <span className="type-label text-text">Event</span>
      <span className="num text-sm text-text">{formatDate(date)}</span>
      <span className="type-meta">{timing}</span>
    </li>
  );
}

/**
 * Which stored sessions the event mapped to, and which sessions each horizon was measured to.
 *
 * Only sessions the API returned appear (event session, anchor, targets); the event marker sits
 * BETWEEN session slots according to its declared timing and is labelled with the timing text. It
 * is never placed at a clock time, and no date is manufactured: a horizon with no stored target
 * session shows its state instead of a date. A refused alignment replaces the ladder with the
 * backend's reason.
 */
export function SessionLadder({ study, label }: { readonly study: Study; readonly label?: string }) {
  const alignment = study.alignment;
  const session = alignment.session_alignment;
  if (session === null || session.status !== "ALIGNED") {
    const status = session?.status ?? "NO_SESSION_DATA";
    return (
      <RefusalNotice
        kind={ALIGNMENT_STATE[status] as NonValueState}
        title="Session alignment"
        reason={session?.reason ?? "The event could not be mapped to a stored session."}
      >
        <span className="type-id text-text-3">{status}</span> · {ALIGNMENT_LABEL[status]}
      </RefusalNotice>
    );
  }
  const timing = TIMING_LABEL[alignment.timing];
  const anchor = session.first_post_event_session;
  const afterClose = session.event_session !== null && anchor !== null && session.event_session !== anchor;
  return (
    <div className="flex flex-col gap-1.5">
      <div
        role="region"
        aria-label={label ? `${label} session ladder, scrollable` : "Session ladder, scrollable"}
        tabIndex={0}
        // `relative`: the ladder scrolls sideways, and the visually-hidden state names inside its slots are
        // absolutely positioned: without a positioned ancestor they escape the scroll box and widen the page.
        className="relative overflow-x-auto pb-1"
      >
        <ol aria-label="Stored trading sessions" className="flex items-stretch gap-2">
        {afterClose && session.event_session ? <Slot label="Event session" date={session.event_session} /> : null}
        <EventMarker date={alignment.event_date} timing={`${timing} (as stated by the source)`} />
        {anchor ? <Slot label="Anchor · first post-event session" date={anchor} /> : null}
        {study.stock_returns.forward_returns.map((entry) =>
          entry.target_session ? (
            <Slot key={entry.horizon} label={`+${entry.horizon} sessions`} date={entry.target_session} />
          ) : (
            <li key={entry.horizon} className="flex min-w-28 shrink-0 flex-col gap-1 rounded border border-dashed border-border px-3 py-2">
              <span className="type-label">+{entry.horizon} sessions</span>
              <DomainState kind={FORWARD_RETURN_STATE[entry.status] as NonValueState} reason={entry.reason} />
              <span className="type-meta">{FORWARD_RETURN_LABEL[entry.status]}</span>
            </li>
          ),
        )}
        </ol>
      </div>
      <p className="type-meta">
        Stored trading sessions only; weekends and holidays are not shown, and slots are not to scale. The event is placed between
        sessions by its declared timing, never at a clock time.
      </p>
    </div>
  );
}
