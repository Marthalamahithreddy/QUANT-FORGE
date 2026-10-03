import Link from "next/link";

import { StatePanel } from "@/components/ui/state-panel";

export default function EarningsEventNotFound() {
  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-4 p-8">
      <StatePanel kind="empty" title="Earnings event not found">
        No stored event has this identifier.
      </StatePanel>
      <Link href="/earnings" className="text-sm text-accent hover:underline">
        ← Earnings explorer
      </Link>
    </main>
  );
}
