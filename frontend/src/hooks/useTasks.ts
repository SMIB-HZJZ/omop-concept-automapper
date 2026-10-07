import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import { invalidateVocabulary, keys } from "@/api/queries";
import type { Task } from "@/api/types";

export const TASK_LABELS: Record<string, string> = {
  "vocabulary.import": "Vocabulary import",
  "atc7.process": "ATC7 processing",
  "source.import": "Source concept import",
  "embeddings.embed": "Embedding",
  "embeddings.sync_metadata": "Metadata sync",
  "embeddings.sync_atc7": "ATC7 sync",
  "embeddings.migrate_qdrant": "Qdrant migration",
  "automap.run": "Auto-mapping",
};

export const taskLabel = (kind: string) => TASK_LABELS[kind] ?? kind;

/** Poll one task every second until it finishes. */
export function useTask(taskId: string | null | undefined) {
  return useQuery({
    queryKey: keys.task(taskId ?? ""),
    queryFn: async () => unwrap(await api.GET("/api/tasks/{task_id}", { params: { path: { task_id: taskId! } } })),
    enabled: !!taskId,
    refetchInterval: (query) => (query.state.data?.is_terminal ? false : 1000),
  });
}

/** Running tasks (all of them for admins, own tasks for users); drives the task tray. */
export function useActiveTasks() {
  return useQuery({
    queryKey: keys.tasks({ active: true }),
    queryFn: async () => unwrap(await api.GET("/api/tasks", { params: { query: { active: true } } })),
    refetchInterval: (query) => ((query.state.data?.length ?? 0) > 0 ? 1500 : 5000),
  });
}

/** The most recent task of a kind, so a page shows progress (or the last result) after a reload. */
export function useLatestTask(kind: string, match?: (task: Task) => boolean) {
  return useQuery({
    queryKey: keys.tasks({ kind, latest: true }),
    queryFn: async () => unwrap(await api.GET("/api/tasks", { params: { query: { kind, limit: 20 } } })),
    select: (tasks) => tasks.find((t) => (match ? match(t) : true)) ?? null,
    refetchInterval: (query) => (query.state.data?.some((t) => !t.is_terminal) ? 1000 : false),
  });
}

export function useCancelTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (taskId: string) =>
      unwrap(await api.POST("/api/tasks/{task_id}/cancel", { params: { path: { task_id: taskId } } })),
    onSuccess: (task) => {
      qc.setQueryData(keys.task(task.id), task);
      void qc.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

/** Refresh whatever a finished task changed. */
export function invalidateForTask(qc: QueryClient, task: Task) {
  void qc.invalidateQueries({ queryKey: ["tasks"] });
  switch (task.kind) {
    case "vocabulary.import":
      void qc.invalidateQueries({ queryKey: keys.vocabularyStatus });
      void qc.invalidateQueries({ queryKey: keys.atc7Stats });
      void qc.invalidateQueries({ queryKey: ["embedding-status"] });
      void qc.invalidateQueries({ queryKey: keys.meta });
      invalidateVocabulary(qc);
      break;
    case "atc7.process":
      void qc.invalidateQueries({ queryKey: keys.atc7Stats });
      break;
    case "source.import":
    case "automap.run": {
      const id = (task.params as { vocabulary_id?: number } | null)?.vocabulary_id;
      invalidateVocabulary(qc, id);
      break;
    }
    default:
      if (task.kind.startsWith("embeddings.")) {
        void qc.invalidateQueries({ queryKey: ["embedding-status"] });
        void qc.invalidateQueries({ queryKey: keys.meta });
      }
  }
}
