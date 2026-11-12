import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import type { TaskRef } from "@/api/types";
import { trackTask } from "@/hooks/taskTracker";

/** Starts a background task, tracks it until it finishes and reports success or errors as toasts.
 * `run` resolves to the task reference, or null when the task could not be started. */
export function useStartTask() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (start: () => Promise<TaskRef>, label: string): Promise<TaskRef | null> => {
      setBusy(true);
      try {
        const ref = await start();
        trackTask(ref.task_id);
        toast.success(`${label} started`);
        return ref;
      } catch (err) {
        // 409: another task of the same group runs; 503: the task queue is unreachable
        toast.error(`Could not start ${label.toLowerCase()}`, { description: errorMessage(err) });
        return null;
      } finally {
        setBusy(false);
        void qc.invalidateQueries({ queryKey: ["tasks"] });
      }
    },
    [qc],
  );

  return { run, busy };
}
