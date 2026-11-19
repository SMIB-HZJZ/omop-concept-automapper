import { CheckCircle2, X } from "lucide-react";
import { Link } from "react-router";

import { taskResult } from "@/api/taskResults";
import type { Task } from "@/api/types";
import { formatDate, formatNumber } from "@/lib/format";

export function AutomapResult({ task }: { task: Task }) {
  const r = taskResult(task, "automap.run");
  if (!r) return null;
  const n = (value: number | undefined) => formatNumber(value ?? 0);
  const outcomes = r.outcomes ?? {};
  return (
    <div className="space-y-1 text-sm">
      <div>
        Processed {n(r.processed)} of {n(r.total_concepts)} concepts · mapped {n(r.mapped)} · queued for review{" "}
        {n(r.queued)} · skipped {n(r.skipped)} · errors {n(r.errors)}
      </div>
      {(r.errors ?? 0) > 0 && (
        <div className="text-destructive">
          {n(r.errors)} concepts could not be processed (LLM or search errors; see the worker log). Run auto-mapping
          again to retry them.
        </div>
      )}
      {Object.keys(outcomes).length > 0 && (
        <div className="text-muted-foreground text-xs">
          Outcomes:{" "}
          {Object.entries(outcomes)
            .map(([k, v]) => `${k} ${v}`)
            .join(", ")}
        </div>
      )}
      {r.queued > 0 && (
        <Link to="/review" className="text-primary underline">
          Review the suggestions
        </Link>
      )}
    </div>
  );
}

/** A finished run as one line: what happened, a link to Review, and a close button. */
export function AutomapDoneLine({ task, onDismiss }: { task: Task; onDismiss: () => void }) {
  const r = taskResult(task, "automap.run");
  const n = (value: number | undefined) => formatNumber(value ?? 0);
  return (
    <div className="border-success/40 bg-success/5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border px-3 py-2 text-sm">
      <CheckCircle2 className="text-success size-4" />
      <span className="font-medium">Auto-mapping finished</span>
      <span className="text-muted-foreground text-xs">{formatDate(task.finished_at)}</span>
      {r && (
        <span>
          {n(r.processed)} terms · {n(r.mapped)} mapped · <b>{n(r.queued)}</b> to review · {n(r.skipped)} without a
          confident match
          {(r.errors ?? 0) > 0 && (
            <span className="text-destructive"> · {n(r.errors)} errors (run again to retry)</span>
          )}
        </span>
      )}
      {r && r.queued > 0 && (
        <Link to="/review" className="text-primary font-medium underline-offset-4 hover:underline">
          Review →
        </Link>
      )}
      <button
        type="button"
        aria-label="Dismiss"
        className="text-muted-foreground hover:text-foreground ml-auto cursor-pointer rounded p-0.5"
        onClick={onDismiss}
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
