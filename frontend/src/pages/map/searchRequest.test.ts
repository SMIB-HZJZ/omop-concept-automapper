import { describe, expect, it } from "vitest";

import type { DrugMatch, UnmappedItem } from "@/api/types";
import { buildSearchRequest, defaultSearchRequest, type SearchInput } from "./searchRequest";

const match = {
  source_id: 1,
  status: "exact",
  search_text: "acetaminophen 500 MG Oral Tablet",
  exclude_classes: ["Branded Drug"],
  resolved: [],
  approximate: [],
  candidates: [],
} as unknown as DrugMatch;

const input: SearchInput = {
  query: "Paracetamol 500 mg tablete",
  edited: false,
  limit: 100,
  domains: [],
  vocabularies: [],
  conceptClasses: [],
  atc7Codes: [],
  isDrug: true,
  drugMatch: match,
  showBranded: false,
};

describe("buildSearchRequest", () => {
  it("adds the drug query and hides branded drugs like auto-mapping", () => {
    const r = buildSearchRequest(input);
    expect(r.extra_queries).toEqual(["acetaminophen 500 MG Oral Tablet"]);
    expect(r.exclude_concept_classes).toEqual(["Branded Drug"]);
    expect(r.atc7_fallback).toBe(true);
  });

  it("searches typed text as typed", () => {
    const r = buildSearchRequest({ ...input, query: "Normabel", edited: true });
    expect(r.extra_queries).toEqual([]);
    expect(r.exclude_concept_classes).toEqual([]);
  });

  it("shows branded drugs when switched on, and adds nothing outside drug vocabularies", () => {
    expect(buildSearchRequest({ ...input, showBranded: true }).exclude_concept_classes).toEqual([]);
    const plain = buildSearchRequest({ ...input, isDrug: false });
    expect(plain.extra_queries).toEqual([]);
    expect(plain.atc7_fallback).toBe(false);
  });

  it("is the panel's first request for the next term (the preload hits the same cache entry)", () => {
    const term = { source_concept_name: " Paracetamol 500 mg tablete ", atc7_codes: [] } as unknown as UnmappedItem;
    expect(defaultSearchRequest(term, true, [], match)).toEqual(buildSearchRequest(input));
  });
});
