import type { components } from "./schema";
import type { Task } from "./types";

type S = components["schemas"];

/** Result payload of each task kind, as documented by the backend (app/schemas/task_results.py). */
export type TaskResults = {
  "vocabulary.import": S["VocabularyImportResult"];
  "source.import": S["SourceImportResult"];
  "embeddings.embed": S["EmbedResult"];
  "embeddings.sync_metadata": S["SyncMetadataResult"];
  "automap.run": S["AutomapResult"];
};

export type TaskKind = keyof TaskResults;

/** The typed result of `task` if it is a `kind` task and has a result (failed tasks may keep one). */
export function taskResult<K extends TaskKind>(task: Task, kind: K): TaskResults[K] | null {
  if (task.kind !== kind || !task.result) return null;
  // The backend validates stored results against these models when a task is read
  return task.result as TaskResults[K];
}

/** Source vocabulary a task worked on, for tasks started from a vocabulary. */
export function taskVocabularyId(task: Task): number | undefined {
  const id = task.params?.vocabulary_id;
  return typeof id === "number" ? id : undefined;
}

/** Problems a finished task reported without failing (e.g. the vector index could not be built).
 * Results stored before the field existed have none. */
export function taskWarnings(task: Task): string[] {
  const warnings = task.result?.warnings;
  return Array.isArray(warnings) ? warnings.filter((w): w is string => typeof w === "string" && w !== "") : [];
}
