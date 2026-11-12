import { Link } from "react-router";

import { taskResult } from "@/api/taskResults";
import type { Task } from "@/api/types";
import { formatNumber } from "@/lib/format";

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
