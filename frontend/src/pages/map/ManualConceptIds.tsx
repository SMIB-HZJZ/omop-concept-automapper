import { useState } from "react";

import { useConceptLookup } from "@/api/queries";
import { ErrorAlert } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDebounce } from "@/hooks/useDebounce";
import type { ConceptLike } from "./types";

/** Look up concept IDs typed by the user (";" or "," separated) and add them to the selection. */
export function ManualConceptIds({
  isSelected,
  onAdd,
}: {
  isSelected: (conceptId: number) => boolean;
  onAdd: (concept: ConceptLike) => void;
}) {
  const [open, setOpen] = useState(false);
  const [ids, setIds] = useState("");
  const debouncedIds = useDebounce(open ? ids.trim() : "", 500);
  const lookup = useConceptLookup(debouncedIds);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Checkbox id="manual-ids" checked={open} onCheckedChange={(v) => setOpen(!!v)} />
        <Label htmlFor="manual-ids">Enter concept IDs manually</Label>
      </div>
      {open && (
        <div className="space-y-2">
          <Input
            aria-label="Concept IDs"
            placeholder="123456 or 123456;789012"
            value={ids}
            onChange={(e) => setIds(e.target.value)}
          />
          <ErrorAlert error={lookup.error} title="Invalid concept IDs" />
          {lookup.data?.missing.map((id) => (
            <p key={id} className="text-destructive text-sm">
              Concept ID {id} not found
            </p>
          ))}
          {lookup.data?.found.map((c) => (
            <div key={c.concept_id} className="flex items-center justify-between rounded border px-3 py-1.5 text-sm">
              <span>
                {c.concept_name}{" "}
                <span className="text-muted-foreground">
                  ({c.concept_id}, {c.domain_id})
                </span>
              </span>
              <Button size="sm" variant="outline" disabled={isSelected(c.concept_id)} onClick={() => onAdd(c)}>
                {isSelected(c.concept_id) ? "Added" : "Add"}
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
