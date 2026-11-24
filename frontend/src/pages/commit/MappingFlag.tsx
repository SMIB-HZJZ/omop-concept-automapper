import { Ban, Flag, MessageCircleQuestion } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useFlagSource } from "@/api/queries";
import type { MappedRow } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDate } from "@/lib/format";

/** A reviewer's question about a mapped term: add one with a note, see who asked, clear it once
 * settled. Flags are kept on the source term, so they also work on a released (locked) set. */
export function MappingFlag({ row, vocabularyId }: { row: MappedRow; vocabularyId: number }) {
  const flag = useFlagSource(vocabularyId);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");

  const save = (value: "question" | null) =>
    flag.mutate(
      { sourceId: row.source_id, flag: value, note: value ? note : null },
      {
        onSuccess: () => {
          toast.message(value ? "Question added" : "Flag cleared");
          setOpen(false);
          setNote("");
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );

  const Icon = row.flag === "unmappable" ? Ban : row.flag ? MessageCircleQuestion : Flag;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={row.flag ? `Flag: ${row.flag_note ?? row.flag}` : "Ask a question about this mapping"}
          title={row.flag ? (row.flag_note ?? row.flag) : "Ask a question about this mapping"}
          className={row.flag ? "text-warning" : "text-muted-foreground"}
          onClick={(e) => e.stopPropagation()}
        >
          <Icon />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 space-y-3" onClick={(e) => e.stopPropagation()}>
        <div className="text-sm font-medium">
          “{row.source_concept_name}” → {row.concept_name}
        </div>
        {row.flag ? (
          <>
            <div className="space-y-1 text-sm">
              <div>
                {row.flag_note ? `“${row.flag_note}”` : row.flag === "question" ? "A question" : "Not mappable"}
              </div>
              <div className="text-muted-foreground text-xs">
                {row.flagged_by ? `by ${row.flagged_by} ` : ""}
                {formatDate(row.flagged_at ?? null)}
              </div>
            </div>
            <Button className="w-full" variant="outline" onClick={() => save(null)} disabled={flag.isPending}>
              Settled: clear the flag
            </Button>
          </>
        ) : (
          <>
            <div className="grid gap-1.5">
              <Label htmlFor={`flag-note-${row.source_id}-${row.concept_id}`}>Question or remark</Label>
              <Input
                id={`flag-note-${row.source_id}-${row.concept_id}`}
                value={note}
                maxLength={1000}
                placeholder="e.g. should this be the narrower concept?"
                onChange={(e) => setNote(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && save("question")}
              />
            </div>
            <Button className="w-full" onClick={() => save("question")} disabled={flag.isPending}>
              <MessageCircleQuestion /> Flag for review
            </Button>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
