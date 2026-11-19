import type { ReactNode } from "react";

import { useConceptModal } from "@/hooks/useConceptModal";
import { cn } from "@/lib/utils";

/** A concept name that opens the concept modal; the click does not select the surrounding row. */
export function ConceptLink({
  conceptId,
  children,
  className,
}: {
  conceptId: number;
  children: ReactNode;
  className?: string;
}) {
  const { openConcept } = useConceptModal();
  return (
    <button
      type="button"
      className={cn(
        "decoration-primary/40 hover:text-primary cursor-pointer text-left underline decoration-dotted underline-offset-4 transition-colors hover:decoration-solid",
        className,
      )}
      onClick={(e) => {
        e.stopPropagation();
        openConcept(conceptId);
      }}
      onKeyDown={(e) => e.stopPropagation()}
      title="Show details, hierarchy and AI explanation"
    >
      {children}
    </button>
  );
}
