import { ChevronDown, Sparkles } from "lucide-react";

import type { SuggestResponse } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useConceptModal } from "@/hooks/useConceptModal";
import { Rationale } from "@/components/Rationale";
import { outcomeLabel } from "@/lib/outcome";
import type { ConceptLike } from "./types";
import { stepName } from "@/lib/methods";

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
  const { openConcept } = useConceptModal();
  // A combined match (e.g. one concept per ingredient) is selected as a whole
  const all: ConceptLike[] = suggestion.selected_concepts?.length
    ? suggestion.selected_concepts
    : suggested
      ? [suggested]
      : [];
  return (
    <Alert>
      <Sparkles />
      <AlertDescription>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${outcomeLabel(suggestion.outcome, threshold).tone}`}
          >
            {outcomeLabel(suggestion.outcome, threshold).text}
          </span>
          {suggested ? (
            <span>
              {all.map((c, i) => (
                <span key={c.concept_id}>
                  {i > 0 && " + "}
                  <b>{c.concept_name}</b> (ID {c.concept_id})
                </span>
              ))}{" "}
              · {suggested.domain_id} · confidence <b>{suggestion.confidence ?? "—"}</b>
              {suggestion.match_type && ` · ${suggestion.match_type} match`}
            </span>
          ) : (
            <span>No concept suggested</span>
          )}
          {suggested && (
            <>
              <Button size="sm" variant="cta" onClick={() => all.forEach(onSelect)}>
                {all.length > 1 ? `Select all ${all.length}` : "Select suggestion"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={() => openConcept(suggested.concept_id, { explain: true })}
              >
                <Sparkles /> Explain
              </Button>
            </>
          )}
          <span className="text-muted-foreground text-xs">
            pipeline {suggestion.pipeline} · threshold {threshold}
          </span>
        </div>
        <Rationale text={suggestion.rationale} className="mt-1 text-sm" />
        <Collapsible className="mt-1">
          <CollapsibleTrigger className="text-muted-foreground flex items-center gap-1 text-xs hover:underline">
            <ChevronDown className="size-3" /> Trace ({suggestion.trace.length} steps)
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ol className="mt-1 space-y-0.5 text-xs">
              {suggestion.trace.map((t, i) => (
                <li key={i}>
                  <span className="font-medium" title={t.node}>
                    {stepName(t.node)}
                  </span>{" "}
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
