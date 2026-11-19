import type { SearchRequest, UnmappedItem } from "@/api/types";

export const DEFAULT_LIMIT = 100;

/** The search the mapping panel runs first for a term; Map uses it to load the next term's
 * candidates early, so it must match the panel's own first request. */
export function defaultSearchRequest(concept: UnmappedItem, isDrug: boolean, domains: string[]): SearchRequest {
  const detected = concept.atc7_codes?.[0] ?? null;
  return {
    query: concept.source_concept_name.trim(),
    k: DEFAULT_LIMIT,
    domains,
    vocabulary_id: null,
    atc7_codes: isDrug && detected ? [detected] : [],
  };
}
