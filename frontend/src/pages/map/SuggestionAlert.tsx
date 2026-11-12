import { ChevronDown, Sparkles } from "lucide-react";

import type { SuggestResponse } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ConceptLike } from "./types";

const OUTCOME_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  accepted: "default",
  rejected: "secondary",
  no_match: "outline",
  no_candidates: "outline",
  error: "destructive",
};

/** Result of "AI suggest": the pick, its confidence and rationale, and the workflow trace. */
export function SuggestionAlert({
  suggestion,
  threshold,
  onSelect,
}: {
  suggestion: SuggestResponse;
  threshold: number;
  onSelect: (concept: ConceptLike) => void;
}) {
  const suggested = suggestion.selected ?? null;
  return (
    <Alert>
      <Sparkles />
      <AlertDescription>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={OUTCOME_VARIANT[suggestion.outcome] ?? "outline"}>{suggestion.outcome}</Badge>
          {suggested ? (
            <span>
              <b>{suggested.concept_name}</b> (ID {suggested.concept_id}, {suggested.domain_id}) · confidence{" "}
              <b>{suggestion.confidence ?? "—"}</b>
            </span>
          ) : (
            <span>No concept suggested</span>
          )}
          {suggested && (
            <Button size="sm" variant="outline" onClick={() => onSelect(suggested)}>
              Select suggestion
            </Button>
          )}
          <span className="text-muted-foreground text-xs">
            pipeline {suggestion.pipeline} · threshold {threshold}
          </span>
        </div>
        {suggestion.rationale && <div className="mt-1 text-sm">{suggestion.rationale}</div>}
        <Collapsible className="mt-1">
          <CollapsibleTrigger className="text-muted-foreground flex items-center gap-1 text-xs hover:underline">
            <ChevronDown className="size-3" /> Trace ({suggestion.trace.length} steps)
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ol className="mt-1 space-y-0.5 text-xs">
              {suggestion.trace.map((t, i) => (
                <li key={i}>
                  <span className="font-mono">{t.node}</span>{" "}
                  <span className="text-muted-foreground tabular-nums">{t.ms} ms</span> — {t.summary}
                </li>
              ))}
            </ol>
          </CollapsibleContent>
        </Collapsible>
      </AlertDescription>
    </Alert>
  );
}
