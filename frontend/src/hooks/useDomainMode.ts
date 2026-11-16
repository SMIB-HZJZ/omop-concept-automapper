import { usePersistentState } from "@/hooks/usePersistentState";

export type DomainMode = "restrict" | "prefer" | "all";

export const DOMAIN_MODES: { value: DomainMode; label: string; hint: string }[] = [
  { value: "restrict", label: "Only these domains", hint: "Never map outside the selected domains." },
  {
    value: "prefer",
    label: "Prefer these domains",
    hint: "Search the selected domains first; a few candidates from other domains are allowed.",
  },
  { value: "all", label: "All domains", hint: "Search every domain and let the reranker decide." },
];

/** Domain preference shared by "Auto map settings" and the per-concept "AI suggest". */
export function useDomainMode() {
  return usePersistentState<DomainMode>("automap-domain-mode", "prefer");
}
