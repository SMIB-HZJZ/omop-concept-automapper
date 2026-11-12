/** Columns every source concept CSV must have (checked again by the backend). */
export const REQUIRED_SOURCE_COLUMNS = ["source_value", "source_concept_name"] as const;

/** Required columns missing from a CSV header (header names are trimmed first). */
export function missingSourceColumns(header: readonly string[]): string[] {
  const present = new Set(header.map((c) => c.trim()));
  return REQUIRED_SOURCE_COLUMNS.filter((c) => !present.has(c));
}
