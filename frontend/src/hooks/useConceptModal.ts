import { createContext, useContext } from "react";

export type OpenConceptOptions = {
  /** Start the AI explanation right away (for explicit "Explain" buttons) */
  explain?: boolean;
};

export type ConceptModalApi = { openConcept: (conceptId: number, options?: OpenConceptOptions) => void };

export const ConceptModalContext = createContext<ConceptModalApi | null>(null);

/** Opens the concept details modal (hierarchy, related concepts, AI explanation on request). */
export function useConceptModal(): ConceptModalApi {
  const ctx = useContext(ConceptModalContext);
  // Outside the provider (e.g. in a unit test) opening is a no-op
  return ctx ?? { openConcept: () => {} };
}
