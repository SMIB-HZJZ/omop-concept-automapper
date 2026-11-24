import { describe, expect, it } from "vitest";

import { alreadyMapped, ApiError, errorMessage } from "./client";

describe("API errors", () => {
  const conflict = {
    code: "already_mapped",
    message: "Already mapped by ana; replace that mapping?",
    mapped_by: "ana",
    mapped_at: null,
    concept_ids: [201],
  };

  it("reads a conflict from a 409 only", () => {
    expect(alreadyMapped(new ApiError("x", 409, conflict))?.mapped_by).toBe("ana");
    expect(alreadyMapped(new ApiError("x", 400, conflict))).toBeNull();
    expect(alreadyMapped(new ApiError("x", 409, "locked"))).toBeNull();
  });

  it("uses an object detail's message", () => {
    expect(errorMessage({ detail: conflict })).toBe(conflict.message);
  });
});
