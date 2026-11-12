import { describe, expect, it } from "vitest";

import { missingSourceColumns } from "./csv";

describe("missingSourceColumns", () => {
  it("is empty when the required columns are present (in any order, with extras)", () => {
    expect(missingSourceColumns(["freq", "source_concept_name", "source_value", "concept_id"])).toEqual([]);
  });

  it("ignores surrounding whitespace in header names", () => {
    expect(missingSourceColumns([" source_value ", "source_concept_name\t"])).toEqual([]);
  });

  it("lists every missing column", () => {
    expect(missingSourceColumns(["source_value"])).toEqual(["source_concept_name"]);
    expect(missingSourceColumns([])).toEqual(["source_value", "source_concept_name"]);
  });
});
