/** One vocabulary in the combined source_to_concept_map export. */
export type ExportChoice = {
  id: number;
  selected: boolean;
  /** Release number; null for the latest */
  release: number | null;
  /** SOURCE_VOCABULARY_ID written for its rows */
  label: string;
};

const LABEL = /^[A-Za-z0-9_-]{1,20}$/;

/** The SOURCE_VOCABULARY_ID the server derives from a name (releases.default_label). */
export function defaultLabel(name: string, id: number): string {
  return (
    name
      .replace(/[^A-Za-z0-9_-]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 20) || `SOURCE_${id}`
  );
}

/** Labels used twice or not valid OMOP vocabulary ids. */
export function labelProblems(choices: ExportChoice[]) {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const c of choices) {
    if (seen.has(c.label)) duplicates.add(c.label);
    seen.add(c.label);
  }
  const invalid = new Set(choices.map((c) => c.label).filter((l) => !LABEL.test(l)));
  return { duplicates, invalid, any: duplicates.size > 0 || invalid.size > 0 };
}

export function combinedExportUrl(choices: ExportChoice[]): string {
  const params = new URLSearchParams();
  for (const c of choices) params.append("vocabulary", `${c.id}:${c.release ?? ""}:${c.label}`);
  return `/api/source_to_concept_map.csv?${params}`;
}
