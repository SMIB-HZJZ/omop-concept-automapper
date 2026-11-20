/** Readable names for how a mapping or suggestion was made (backend `mapping_method` codes). */
const METHOD_LABELS: Record<string, string> = {
  manual: "By hand",
  auto_standard: "AI pick",
  auto_drug: "AI pick (drugs)",
  auto_drug_structured: "Drug match",
  auto_exact_name: "Exact name",
  auto_code: "Source code",
  auto_previous: "Earlier mapping",
  auto_agentic: "Agent retry",
  auto_final: "Final decision",
};

export function methodLabel(method: string | null | undefined): string {
  if (!method) return "—";
  return METHOD_LABELS[method] ?? method;
}

/** Readable names of the pipeline steps in an AI suggest trace (node ids of the built-in
 *  pipelines); other ids are shown as they are. */
const STEP_NAMES: Record<string, string> = {
  extract_atc7: "ATC7 code",
  match_previous: "Earlier mappings",
  match_code: "Source code",
  match_drug: "Drug match",
  translate: "Translation",
  match_name: "Exact name",
  retrieve: "Search",
  rerank: "AI pick",
  gate: "Confidence check",
  rewrite: "Rewritten searches",
  rerank_again: "AI pick on new candidates",
  gate_again: "Confidence check",
  refine: "Agent retry",
  final_gate: "Confidence check",
  decide: "Final decision on all evidence",
  last_gate: "Confidence check",
};

export function stepName(nodeId: string): string {
  return STEP_NAMES[nodeId] ?? nodeId;
}
