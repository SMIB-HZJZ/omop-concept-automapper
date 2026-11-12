import { describe, expect, it } from "vitest";

import { taskResult, taskVocabularyId } from "./taskResults";
import type { Task } from "./types";

const task = (overrides: Partial<Task>): Task => ({
  id: "t1",
  kind: "automap.run",
  status: "SUCCESS",
  progress_current: 0,
  is_terminal: true,
  ...overrides,
});

describe("taskResult", () => {
  it("returns the result for the matching kind", () => {
    const t = task({ result: { vocabulary_id: 3, total_concepts: 2, processed: 2, mapped: 1, queued: 0, errors: 1 } });
    expect(taskResult(t, "automap.run")?.errors).toBe(1);
  });

  it("returns null for another kind or a missing result", () => {
    expect(taskResult(task({ result: { drugs_with_atc7: 1 } }), "atc7.process")).toBeNull();
    expect(taskResult(task({ kind: "atc7.process", result: null }), "atc7.process")).toBeNull();
  });
});

describe("taskVocabularyId", () => {
  it("reads a numeric vocabulary_id param", () => {
    expect(taskVocabularyId(task({ params: { vocabulary_id: 7 } }))).toBe(7);
    expect(taskVocabularyId(task({ params: { vocabulary_id: "7" } }))).toBeUndefined();
    expect(taskVocabularyId(task({ params: null }))).toBeUndefined();
  });
});
