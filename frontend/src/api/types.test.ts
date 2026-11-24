import { describe, expect, it } from "vitest";

import { asMatchType } from "./types";

describe("asMatchType", () => {
  it("keeps known match types and falls back otherwise", () => {
    expect(asMatchType("broader")).toBe("broader");
    expect(asMatchType(null)).toBe("exact");
    expect(asMatchType("close", "related")).toBe("related");
  });
});
