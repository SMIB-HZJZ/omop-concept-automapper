import { CheckCircle2, Loader2, XCircle, Ban } from "lucide-react";
import type { ReactNode } from "react";

import type { Task } from "@/api/types";
import { formatDate } from "@/lib/format";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { taskLabel, useCancelTask } from "@/hooks/useTasks";

export function TaskStatusBadge({ status }: { status: string }) {
  const variant =
    status === "SUCCESS"
      ? "default"
      : status === "FAILURE"
        ? "destructive"
        : status === "CANCELLED"
          ? "outline"
          : "secondary";
  return <Badge variant={variant}>{status.toLowerCase()}</Badge>;
}

/** Progress for a background task: bar, message, cancel button, and the outcome once finished.
 * `renderResult` describes the result; it is also shown for failed tasks that kept a partial result. */
export function TaskProgress({
  task,
  renderResult,
  compact,
}: {
  task: Task;
  renderResult?: (task: Task) => ReactNode;
  compact?: boolean;
}) {
  const cancel = useCancelTask();
  const running = !task.is_terminal;

  if (task.status === "FAILURE") {
    return (
      <Alert variant="destructive">
        <XCircle />
        <AlertTitle>{taskLabel(task.kind)} failed</AlertTitle>
        <AlertDescription>
          <span className="break-all">{task.error ?? task.message}</span>
          {renderResult && task.result && <div className="mt-1">{renderResult(task)}</div>}
          <span className="text-xs">Finished {formatDate(task.finished_at)}</span>
        </AlertDescription>
      </Alert>
    );
  }
  if (task.status === "CANCELLED") {
    return (
      <Alert>
        <Ban />
        <AlertTitle>{taskLabel(task.kind)} cancelled</AlertTitle>
        <AlertDescription>{task.message}</AlertDescription>
      </Alert>
    );
  }
  if (task.status === "SUCCESS") {
    return (
      <Alert className="border-success/40">
        <CheckCircle2 className="text-success" />
        <AlertTitle>
          {taskLabel(task.kind)} finished{" "}
          <span className="text-muted-foreground font-normal">· {formatDate(task.finished_at)}</span>
        </AlertTitle>
        {renderResult && task.result && <AlertDescription>{renderResult(task)}</AlertDescription>}
      </Alert>
    );
  }

  const pct = task.percentage ?? null;
  return (
    <div className={compact ? "space-y-1" : "space-y-2 rounded-lg border p-4"}>
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className="flex min-w-0 items-center gap-2 font-medium">
          <Loader2 className="size-4 shrink-0 animate-spin" />
          <span className="truncate">{task.message ?? `${taskLabel(task.kind)} queued…`}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {pct !== null && <span className="text-muted-foreground tabular-nums">{pct.toFixed(1)}%</span>}
          {running && (
            <Button
              size="sm"
              variant="outline"
              disabled={cancel.isPending || task.message === "Cancelling…"}
              onClick={() => cancel.mutate(task.id)}
            >
              Cancel
            </Button>
          )}
        </span>
      </div>
      <Progress value={pct ?? undefined} className={pct === null ? "animate-pulse" : undefined} />
      {task.progress_total ? (
        <div className="text-muted-foreground text-xs tabular-nums">
          {task.progress_current.toLocaleString()} / {task.progress_total.toLocaleString()}
        </div>
      ) : null}
    </div>
  );
}
