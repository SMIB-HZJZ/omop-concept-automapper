import type { ConceptFacet } from "@/api/types";

export type ConceptFilterValues = { domains: string[]; vocabularies: string[]; conceptClasses: string[] };

const FIELDS = {
  domains: "domain_id",
  vocabularies: "vocabulary_id",
  conceptClasses: "concept_class_id",
} as const satisfies Record<keyof ConceptFilterValues, keyof ConceptFacet>;

/** Options for one filter, narrowed by the other two as in Athena: only values that standard
 *  concepts matching the other selections have. Selected values always stay listed. */
export function facetOptions(
  facets: ConceptFacet[],
  field: keyof ConceptFilterValues,
  values: ConceptFilterValues,
): string[] {
  const others = (Object.keys(FIELDS) as (keyof ConceptFilterValues)[]).filter((f) => f !== field);
  const found = new Set<string>(values[field]);
  for (const facet of facets) {
    if (others.every((f) => values[f].length === 0 || values[f].includes(facet[FIELDS[f]]))) {
      found.add(facet[FIELDS[field]]);
    }
  }
  return [...found].sort((a, b) => a.localeCompare(b));
}
