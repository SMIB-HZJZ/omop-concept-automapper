import { describe, expect, it } from "vitest";

import { isValidAtc7, normalizeAtc7 } from "./atc7";

describe("ATC7 validation", () => {
  it("accepts well-formed codes, case-insensitively", () => {
    expect(isValidAtc7("A10BA02")).toBe(true);
    expect(isValidAtc7(" n02be01 ")).toBe(true);
  });

  it("rejects other ATC levels and malformed codes", () => {
    for (const code of ["", "A10BA", "A10BA021", "110BA02", "A1OBA02", "A10B102"]) {
      expect(isValidAtc7(code)).toBe(false);
    }
  });

  it("normalizes whitespace and case", () => {
    expect(normalizeAtc7(" a10ba02 ")).toBe("A10BA02");
  });
});
