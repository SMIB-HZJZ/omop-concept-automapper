import { Check, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { ConceptLink } from "@/components/ConceptLink";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { MATCH_TYPES, type MatchType } from "@/api/types";
import { cn } from "@/lib/utils";
import type { ConceptLike } from "./types";
import { useSelectedVocabulary } from "@/hooks/useSelectedVocabulary";

/** The concepts chosen for the current source term, with "Confirm mapping" (kept at the top of the
 * panel, so confirming never needs scrolling). */
export function SelectedConcepts({
  concepts,
  onRemove,
  onConfirm,
  confirming,
  matchType,
  onMatchTypeChange,
}: {
  concepts: ConceptLike[];
  onRemove: (conceptId: number) => void;
  onConfirm: () => void;
  confirming: boolean;
  /** How the selected concept(s) relate to the source term */
  matchType: MatchType;
  onMatchTypeChange: (value: MatchType) => void;
}) {
  const empty = concepts.length === 0;
  // A released vocabulary is locked until an admin starts the next release
  const locked = useSelectedVocabulary().vocabulary?.locked ?? false;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-xl border p-3 transition-colors",
        empty ? "bg-muted/40 border-dashed" : "border-primary/30 bg-accent/50",
      )}
    >
      <div className="text-sm font-medium">{empty ? "Nothing selected yet" : `Map to (${concepts.length})`}</div>
      {empty && (
        <span className="text-muted-foreground text-sm">
          Tick concepts in the table below, or use the AI suggestion.
        </span>
      )}
      <AnimatePresence initial={false}>
        {concepts.map((c) => (
          <motion.span
            key={c.concept_id}
            layout
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.9 }}
            transition={{ duration: 0.15 }}
            className="bg-background inline-flex max-w-full items-center gap-1.5 rounded-full border py-1 pr-1 pl-3 text-sm shadow-xs"
          >
            <ConceptLink conceptId={c.concept_id} className="max-w-72 truncate">
              {c.concept_name}
            </ConceptLink>
            <span className="text-muted-foreground text-xs tabular-nums">{c.concept_id}</span>
            <button
              type="button"
              aria-label={`Remove ${c.concept_name}`}
              className="text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer rounded-full p-0.5"
              onClick={() => onRemove(c.concept_id)}
            >
              <X className="size-3.5" />
            </button>
          </motion.span>
        ))}
      </AnimatePresence>
      {!empty && (
        <Select value={matchType} onValueChange={(v) => onMatchTypeChange(v as MatchType)}>
          <SelectTrigger
            className="ml-auto h-8 w-40"
            aria-label="Match type"
            title="How the selected concept relates to the source term"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {MATCH_TYPES.map((t) => (
              <SelectItem key={t} value={t}>
                {t[0].toUpperCase() + t.slice(1)} match
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Button
        variant="cta"
        className={cn("gap-1.5", empty && "ml-auto")}
        onClick={onConfirm}
        disabled={empty || confirming || locked}
        title={locked ? "This vocabulary is released and locked" : undefined}
      >
        <Check /> {confirming ? "Saving…" : "Confirm mapping"}
      </Button>
    </div>
  );
}
