/** A search result, looked-up concept or AI suggestion that can be selected for mapping. */
export type ConceptLike = {
  concept_id: number;
  concept_name: string;
  domain_id: string;
  vocabulary_id: string;
  concept_class_id?: string;
  concept_code?: string;
  atc7_codes?: string[] | null;
  score?: number;
  /** Set for drugs found by the structured drug match (not by the similarity search) */
  match?: "exact" | "tie" | "approximate";
};
