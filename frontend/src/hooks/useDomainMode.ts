import { usePersistentState } from "@/hooks/usePersistentState";

export type DomainMode = "restrict" | "prefer" | "all";

/** Applies to the target domains and the target vocabularies alike. */
export const DOMAIN_MODES: { value: DomainMode; label: string; hint: string }[] = [
  { value: "restrict", label: "Only these", hint: "Never map outside the selected domains and vocabularies." },
  {
    value: "prefer",
    label: "Preferred",
    hint: "Search the selected domains and vocabularies first; a few candidates from outside are allowed.",
  },
  {
    value: "all",
    label: "All",
    hint: "Search every domain and vocabulary and let the reranker decide.",
  },
];

/** Domain (and vocabulary) preference shared by "Auto map settings" and the per-concept "AI suggest". */
export function useDomainMode() {
  return usePersistentState<DomainMode>("automap-domain-mode", "prefer");
}
