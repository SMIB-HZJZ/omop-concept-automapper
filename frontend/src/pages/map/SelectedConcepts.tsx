import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ConceptLike } from "./types";

/** The concepts chosen for the current source concept, with "Confirm mapping". */
export function SelectedConcepts({
  concepts,
  onRemove,
  onConfirm,
  confirming,
}: {
  concepts: ConceptLike[];
  onRemove: (conceptId: number) => void;
  onConfirm: () => void;
  confirming: boolean;
}) {
  return (
    <div className="space-y-3">
      <div className="text-sm font-medium">Selected concepts for mapping</div>
      {concepts.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          Select concepts from the table to map them to this source concept.
        </p>
      ) : (
        <>
          {concepts.map((c, i) => (
            <div key={c.concept_id} className="relative rounded-md border p-3 text-sm">
              <button
                type="button"
                aria-label={`Remove ${c.concept_name}`}
                className="text-muted-foreground hover:text-foreground absolute top-2 right-2"
                onClick={() => onRemove(c.concept_id)}
              >
                <X className="size-4" />
              </button>
              <div className="pr-5 font-medium">
                {i + 1}. {c.concept_name}
              </div>
              <div className="text-muted-foreground text-xs">
                ID: {c.concept_id} | Domain: {c.domain_id}
              </div>
              <div className="text-muted-foreground text-xs">Vocabulary: {c.vocabulary_id}</div>
            </div>
          ))}
          <Button className="w-full" onClick={onConfirm} disabled={confirming}>
            Confirm mapping
          </Button>
        </>
      )}
    </div>
  );
}
