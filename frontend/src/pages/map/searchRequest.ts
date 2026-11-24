import type { SearchRequest, UnmappedItem } from "@/api/types";

export const DEFAULT_LIMIT = 100;

/** The search the mapping panel runs first for a term; Map uses it to load the next term's
 * candidates early, so it must match the panel's own first request. (A drug with a structure
 * from the CSV is searched only once its structure is matched; Map loads that match early.) */
export function defaultSearchRequest(concept: UnmappedItem, isDrug: boolean, domains: string[]): SearchRequest {
  const detected = concept.atc7_codes?.[0] ?? null;
  return {
    query: concept.source_concept_name.trim(),
    k: DEFAULT_LIMIT,
    domains,
    vocabularies: [],
    concept_classes: [],
    atc7_codes: isDrug && detected ? [detected] : [],
    extra_queries: [],
    exclude_concept_classes: [],
    atc7_fallback: isDrug,
  };
}
