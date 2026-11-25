import type { DrugMatch, SearchRequest, UnmappedItem } from "@/api/types";

export const DEFAULT_LIMIT = 100;

/** What the mapping panel's search is built from. */
export type SearchInput = {
  /** The text in the search box (the source term until the user edits it) */
  query: string;
  /** The user typed their own text: searched as typed, without the drug additions */
  edited: boolean;
  limit: number;
  domains: string[];
  vocabularies: string[];
  conceptClasses: string[];
  atc7Codes: string[];
  isDrug: boolean;
  /** The term's structured drug match, once known */
  drugMatch?: DrugMatch;
  /** The "Show branded drugs" switch */
  showBranded: boolean;
  /** The source term in English (not for drugs, which search their structure instead) */
  english?: string | null;
};

/** The panel's search request. As auto-mapping: for a drug, also search the structure written
 * like an RxNorm name, leave branded drugs out when the term names no brand, and drop a fruitless
 * ATC7 filter. Other terms also search their English rendering. Text the user typed is searched as
 * typed (it may be a brand name). */
export function buildSearchRequest(p: SearchInput): SearchRequest {
  const dm = p.isDrug && !p.edited ? p.drugMatch : undefined;
  return {
    query: p.query,
    k: p.limit,
    domains: p.domains,
    vocabularies: p.vocabularies,
    concept_classes: p.conceptClasses,
    atc7_codes: p.atc7Codes,
    extra_queries: p.edited
      ? []
      : [...(dm?.search_text ? [dm.search_text] : []), ...(!p.isDrug && p.english ? [p.english] : [])],
    exclude_concept_classes: dm && !p.showBranded ? (dm.exclude_classes ?? []) : [],
    atc7_fallback: p.isDrug,
  };
}

/** The search the mapping panel runs first for a term; Map uses it to load the next term's
 * candidates early, so it must match the panel's own first request. A drug with a structure from
 * the CSV is searched once its structure is matched: pass that match. */
export function defaultSearchRequest(
  concept: UnmappedItem,
  isDrug: boolean,
  domains: string[],
  drugMatch?: DrugMatch,
  english?: string | null,
): SearchRequest {
  const detected = concept.atc7_codes?.[0] ?? null;
  return buildSearchRequest({
    query: concept.source_concept_name.trim(),
    edited: false,
    limit: DEFAULT_LIMIT,
    domains,
    vocabularies: [],
    conceptClasses: [],
    atc7Codes: isDrug && detected ? [detected] : [],
    isDrug,
    drugMatch,
    showBranded: false,
    english,
  });
}
