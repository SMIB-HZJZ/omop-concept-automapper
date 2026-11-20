import { describe, expect, it } from "vitest";

import { facetOptions } from "./conceptFacets";

const facets = [
  { domain_id: "Drug", vocabulary_id: "RxNorm", concept_class_id: "Clinical Drug" },
  { domain_id: "Drug", vocabulary_id: "RxNorm", concept_class_id: "Ingredient" },
  { domain_id: "Condition", vocabulary_id: "SNOMED", concept_class_id: "Clinical Finding" },
  { domain_id: "Measurement", vocabulary_id: "LOINC", concept_class_id: "Lab Test" },
  { domain_id: "Measurement", vocabulary_id: "SNOMED", concept_class_id: "Procedure" },
];
const none = { domains: [], vocabularies: [], conceptClasses: [] };

describe("facetOptions", () => {
  it("lists every value without other selections", () => {
    expect(facetOptions(facets, "vocabularies", none)).toEqual(["LOINC", "RxNorm", "SNOMED"]);
  });

  it("narrows a filter by the other selections", () => {
    expect(facetOptions(facets, "vocabularies", { ...none, domains: ["Measurement"] })).toEqual(["LOINC", "SNOMED"]);
    expect(facetOptions(facets, "conceptClasses", { ...none, domains: ["Drug"] })).toEqual([
      "Clinical Drug",
      "Ingredient",
    ]);
    expect(
      facetOptions(facets, "domains", { ...none, vocabularies: ["SNOMED"], conceptClasses: ["Procedure"] }),
    ).toEqual(["Measurement"]);
  });

  it("keeps selected values even when the others rule them out", () => {
    expect(facetOptions(facets, "vocabularies", { ...none, domains: ["Drug"], vocabularies: ["LOINC"] })).toEqual([
      "LOINC",
      "RxNorm",
    ]);
  });
});
