import { describe, expect, it } from "vitest";

import { combinedExportUrl, defaultLabel, labelProblems } from "./exportUrl";

describe("combined export", () => {
  it("derives the label as the server does", () => {
    expect(defaultLabel("Diagnoses (GP)", 3)).toBe("Diagnoses_GP");
    expect(defaultLabel("Šifre", 4)).toBe("ifre");
    expect(defaultLabel("!!!", 5)).toBe("SOURCE_5");
    expect(defaultLabel("A very long vocabulary name", 6)).toBe("A_very_long_vocabula");
  });

  it("flags labels used twice or not valid", () => {
    const c = (id: number, label: string) => ({ id, selected: true, release: null, label });
    expect(labelProblems([c(1, "LAB"), c(2, "LAB")]).duplicates).toEqual(new Set(["LAB"]));
    expect(labelProblems([c(1, "LAB X")]).any).toBe(true);
    expect(labelProblems([c(1, "LAB"), c(2, "DG")]).any).toBe(false);
  });

  it("builds one item per vocabulary", () => {
    const url = combinedExportUrl([
      { id: 1, selected: true, release: null, label: "LAB" },
      { id: 2, selected: true, release: 3, label: "DG" },
    ]);
    expect(url).toBe("/api/source_to_concept_map.csv?vocabulary=1%3A%3ALAB&vocabulary=2%3A3%3ADG");
  });
});
