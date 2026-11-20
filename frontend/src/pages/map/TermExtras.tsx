import { Ban, Check, Flag, MessageCircleQuestion, Plus, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useFlagSource } from "@/api/queries";
import type { Suggestion, UnmappedItem } from "@/api/types";
import { ConceptLink } from "@/components/ConceptLink";
import { Rationale } from "@/components/Rationale";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/** An AI suggestion waiting for this term (from auto-mapping in review mode), shown in place. */
export function WaitingSuggestion({
  suggestion,
  busy,
  onAccept,
  onReject,
}: {
  suggestion: Suggestion;
  busy: boolean;
  onAccept: () => void;
  onReject: () => void;
}) {
  const tone =
    suggestion.confidence >= 8 ? "text-success" : suggestion.confidence >= 5 ? "text-warning" : "text-destructive";
  return (
    <div className="from-brand-from/[0.06] to-brand-to/[0.08] rounded-xl border bg-gradient-to-br p-3">
      <div className="flex flex-wrap items-start gap-3">
        <Sparkles className="text-primary mt-0.5 size-4 shrink-0" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="text-sm">
            <span className="text-muted-foreground">AI suggests </span>
            <ConceptLink conceptId={suggestion.concept_id} className="font-semibold">
              {suggestion.concept_name}
            </ConceptLink>
            {(suggestion.extra_concepts ?? []).map((c) => (
              <span key={c.concept_id}>
                {" + "}
                <ConceptLink conceptId={c.concept_id} className="font-semibold">
                  {c.concept_name}
                </ConceptLink>
              </span>
            ))}
            <span className="text-muted-foreground">
              {" "}
              · confidence <b className={tone}>{suggestion.confidence}</b>
              {suggestion.match_type && suggestion.match_type !== "exact" ? ` · ${suggestion.match_type} match` : ""}
            </span>
          </div>
          <Rationale text={suggestion.rationale} className="text-muted-foreground text-xs leading-relaxed" />
        </div>
        <div className="flex shrink-0 gap-1.5">
          <Button size="sm" variant="cta" className="gap-1" disabled={busy} onClick={onAccept}>
            <Check /> Accept
          </Button>
          <Button size="sm" variant="outline" className="gap-1" disabled={busy} onClick={onReject}>
            <X /> Reject
          </Button>
        </div>
      </div>
    </div>
  );
}

/** The latest auto-mapping pick for this term that stayed under the confidence threshold: not in
 *  the review queue, shown as a hint the mapper can take over. */
export function BelowThresholdPick({ suggestion, onSelect }: { suggestion: Suggestion; onSelect: () => void }) {
  return (
    <div className="rounded-xl border border-dashed p-3">
      <div className="flex flex-wrap items-start gap-3">
        <Sparkles className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="text-sm">
            <span className="text-muted-foreground">Last auto-mapping picked </span>
            <ConceptLink conceptId={suggestion.concept_id} className="font-semibold">
              {suggestion.concept_name}
            </ConceptLink>
            {(suggestion.extra_concepts ?? []).map((c) => (
              <span key={c.concept_id}>
                {" + "}
                <ConceptLink conceptId={c.concept_id} className="font-semibold">
                  {c.concept_name}
                </ConceptLink>
              </span>
            ))}
            <span className="text-muted-foreground">
              {" "}
              · confidence <b className="text-warning">{suggestion.confidence}</b>, below the threshold
              {suggestion.match_type && suggestion.match_type !== "exact" ? ` · ${suggestion.match_type} match` : ""}
            </span>
          </div>
          <Rationale text={suggestion.rationale} className="text-muted-foreground text-xs leading-relaxed" />
          <div className="text-muted-foreground text-xs">{formatDate(suggestion.created_at)}</div>
        </div>
        <Button size="sm" variant="outline" className="shrink-0 gap-1" onClick={onSelect}>
          <Plus /> Select
        </Button>
      </div>
    </div>
  );
}

const FLAGS = [
  { value: "question", label: "Ask a colleague", icon: MessageCircleQuestion },
  { value: "unmappable", label: "Can't be mapped", icon: Ban },
] as const;

/** Flag a term that cannot be mapped now; it leaves the to-do list (and auto-mapping skips it). */
export function FlagButton({
  concept,
  vocabularyId,
  onFlagged,
}: {
  concept: UnmappedItem;
  vocabularyId: number;
  onFlagged: () => void;
}) {
  const flag = useFlagSource(vocabularyId);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"question" | "unmappable">("question");
  const [note, setNote] = useState("");

  const save = () =>
    flag.mutate(
      { sourceId: concept.source_id, flag: kind, note },
      {
        onSuccess: () => {
          toast.success(kind === "question" ? "Flagged for a colleague" : "Flagged as not mappable", {
            description: "Find it under Flagged.",
          });
          setOpen(false);
          setNote("");
          onFlagged();
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" className="gap-2" title="Skip this term and flag it">
          <Flag className="size-4" /> Flag
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 space-y-3">
        <div className="text-sm font-medium">Flag “{concept.source_concept_name}”</div>
        <div className="grid grid-cols-2 gap-2">
          {FLAGS.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              onClick={() => setKind(value)}
              className={cn(
                "flex cursor-pointer flex-col items-center gap-1 rounded-lg border p-2 text-xs transition-colors",
                kind === value ? "border-primary bg-accent text-accent-foreground" : "hover:bg-muted",
              )}
            >
              <Icon className="size-4" />
              {label}
            </button>
          ))}
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="flag-note">Note (optional)</Label>
          <Input
            id="flag-note"
            value={note}
            maxLength={1000}
            placeholder={kind === "question" ? "e.g. brand or generic?" : "e.g. local administrative code"}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
        </div>
        <Button className="w-full" onClick={save} disabled={flag.isPending}>
          Flag and go to the next term
        </Button>
      </PopoverContent>
    </Popover>
  );
}

/** Shown on a flagged term: why, by whom, and a way to clear the flag. */
export function FlagBanner({ concept, vocabularyId }: { concept: UnmappedItem; vocabularyId: number }) {
  const flag = useFlagSource(vocabularyId);
  if (!concept.flag) return null;
  const question = concept.flag === "question";
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2 text-sm",
        question ? "border-warning/40 bg-warning/10" : "border-destructive/30 bg-destructive/10",
      )}
    >
      {question ? (
        <MessageCircleQuestion className="text-warning size-4" />
      ) : (
        <Ban className="text-destructive size-4" />
      )}
      <span className="font-medium">{question ? "Waiting for a colleague" : "Marked as not mappable"}</span>
      {concept.flag_note && <span>“{concept.flag_note}”</span>}
      <span className="text-muted-foreground text-xs">
        {concept.flagged_by ? `by ${concept.flagged_by}` : ""} {formatDate(concept.flagged_at ?? null)}
      </span>
      <Button
        size="sm"
        variant="ghost"
        className="ml-auto"
        disabled={flag.isPending}
        onClick={() =>
          flag.mutate(
            { sourceId: concept.source_id, flag: null },
            { onSuccess: () => toast.message("Flag cleared"), onError: (err) => toast.error(errorMessage(err)) },
          )
        }
      >
        Clear flag
      </Button>
    </div>
  );
}
