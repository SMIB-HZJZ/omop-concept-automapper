import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ChevronRight,
  Copy,
  GitBranch,
  Info,
  Link2,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useConceptDetails, useConceptExplanation } from "@/api/queries";
import type { Concept, ConceptDetails, RelatedConcept } from "@/api/types";
import { ErrorAlert } from "@/components/common";
import { Skeleton } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConceptModalContext, type OpenConceptOptions } from "@/hooks/useConceptModal";

/** Provides openConcept() to the app and renders the concept modal. */
export function ConceptModalProvider({ children }: { children: ReactNode }) {
  // A stack, so the modal can go back after following a parent, child or relationship
  const [stack, setStack] = useState<{ id: number; explain: boolean }[]>([]);
  const openConcept = useCallback(
    (id: number, options?: OpenConceptOptions) => setStack([{ id, explain: !!options?.explain }]),
    [],
  );
  const api = useMemo(() => ({ openConcept }), [openConcept]);
  const current = stack.length ? stack[stack.length - 1] : null;

  return (
    <ConceptModalContext.Provider value={api}>
      {children}
      <Dialog open={current !== null} onOpenChange={(open) => !open && setStack([])}>
        <DialogContent className="flex max-h-[88vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
          {current !== null && (
            <ConceptView
              key={`${stack.length}:${current.id}`}
              conceptId={current.id}
              explainOnOpen={current.explain}
              canGoBack={stack.length > 1}
              onBack={() => setStack((s) => s.slice(0, -1))}
              onOpen={(id) => setStack((s) => (s[s.length - 1]?.id === id ? s : [...s, { id, explain: false }]))}
            />
          )}
        </DialogContent>
      </Dialog>
    </ConceptModalContext.Provider>
  );
}

function standardLabel(c: Concept) {
  if (c.standard_concept === "S")
    return { text: "Standard", className: "bg-success/15 text-success border-success/30" };
  if (c.standard_concept === "C") return { text: "Classification", className: "bg-accent text-accent-foreground" };
  return { text: "Non-standard", className: "bg-warning/15 text-warning border-warning/30" };
}

function ConceptView({
  conceptId,
  explainOnOpen,
  canGoBack,
  onBack,
  onOpen,
}: {
  conceptId: number;
  explainOnOpen: boolean;
  canGoBack: boolean;
  onBack: () => void;
  onOpen: (id: number) => void;
}) {
  const details = useConceptDetails(conceptId);
  // The explanation costs an LLM call: only on request (the header button or an "Explain" button)
  const [explain, setExplain] = useState(explainOnOpen);
  const d = details.data;
  const c = d?.concept;

  return (
    <>
      <DialogHeader className="border-b px-6 pt-6 pb-4 text-left">
        <div className="flex items-start gap-3 pr-8">
          {canGoBack && (
            <Button variant="ghost" size="icon-sm" onClick={onBack} aria-label="Back to the previous concept">
              <ArrowLeft />
            </Button>
          )}
          <div className="min-w-0 flex-1">
            {c && !explain && (
              <Button variant="cta" size="sm" className="float-right ml-3 gap-1.5" onClick={() => setExplain(true)}>
                <Sparkles /> Explain with AI
              </Button>
            )}
            {c ? (
              <>
                <DialogTitle className="text-lg leading-snug">{c.concept_name}</DialogTitle>
                <DialogDescription asChild>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Badge variant="outline" className={standardLabel(c).className}>
                      {standardLabel(c).text}
                    </Badge>
                    {c.invalid_reason && (
                      <Badge variant="destructive" className="gap-1">
                        <TriangleAlert /> Invalid ({c.invalid_reason})
                      </Badge>
                    )}
                    <Badge variant="secondary">{c.domain_id}</Badge>
                    <Badge variant="secondary">{c.vocabulary_id}</Badge>
                    <Badge variant="secondary">{c.concept_class_id}</Badge>
                    <CopyChip label="ID" value={String(c.concept_id)} />
                    <CopyChip label="Code" value={c.concept_code} />
                  </div>
                </DialogDescription>
              </>
            ) : (
              <>
                <DialogTitle className="sr-only">Concept {conceptId}</DialogTitle>
                <DialogDescription className="sr-only">Loading the concept</DialogDescription>
                <Skeleton className="h-6 w-2/3" />
                <div className="mt-3 flex gap-1.5">
                  {[0, 1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-5 w-20 rounded-full" />
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </DialogHeader>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        {details.isError ? (
          <ErrorAlert error={details.error} title="Could not load the concept" />
        ) : !d ? (
          <div className="space-y-3">
            <Skeleton className="h-9 w-80" />
            <Skeleton className="h-24" />
            <Skeleton className="h-40" />
          </div>
        ) : (
          <>
            <AnimatePresence initial={false}>
              {explain && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                  className="overflow-hidden"
                >
                  <div className="pb-4">
                    <Explanation conceptId={conceptId} onClose={() => setExplain(false)} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
            <Tabs defaultValue="overview">
              <TabsList className="mb-4">
                <TabsTrigger value="overview" className="gap-1.5">
                  <Info className="size-4" /> Overview
                </TabsTrigger>
                <TabsTrigger value="hierarchy" className="gap-1.5">
                  <GitBranch className="size-4" /> Hierarchy
                  <CountBadge n={d.parent_count + d.child_count} />
                </TabsTrigger>
                <TabsTrigger value="related" className="gap-1.5">
                  <Link2 className="size-4" /> Related
                  <CountBadge n={d.related.length} />
                </TabsTrigger>
              </TabsList>
              <TabsContent value="overview">
                <Overview details={d} onOpen={onOpen} />
              </TabsContent>
              <TabsContent value="hierarchy">
                <Hierarchy details={d} onOpen={onOpen} />
              </TabsContent>
              <TabsContent value="related">
                <Related related={d.related} onOpen={onOpen} />
              </TabsContent>
            </Tabs>
          </>
        )}
      </div>
    </>
  );
}

function CountBadge({ n }: { n: number }) {
  if (!n) return null;
  return <span className="bg-muted text-muted-foreground rounded-full px-1.5 text-[11px] tabular-nums">{n}</span>;
}

function CopyChip({ label, value }: { label: string; value: string }) {
  return (
    <button
      type="button"
      className="text-muted-foreground hover:text-foreground hover:bg-muted inline-flex cursor-pointer items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-xs transition-colors"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => toast.success(`${label} ${value} copied`));
      }}
      title={`Copy ${label.toLowerCase()}`}
    >
      {label} {value} <Copy className="size-3" />
    </button>
  );
}

function Overview({ details, onOpen }: { details: ConceptDetails; onOpen: (id: number) => void }) {
  const c = details.concept;
  const mapsTo = details.related.filter((r) => r.relationship_id === "Maps to" && r.concept_id !== c.concept_id);
  return (
    <div className="space-y-5">
      {c.standard_concept !== "S" && mapsTo.length > 0 && (
        <Section title="Maps to (standard concept to use instead)">
          <ConceptList items={mapsTo} onOpen={onOpen} />
        </Section>
      )}

      <Section title={`Synonyms${details.synonyms.length ? ` (${details.synonyms.length})` : ""}`}>
        {details.synonyms.length ? (
          <div className="flex flex-wrap gap-1.5">
            {details.synonyms.map((s) => (
              <Badge
                key={s}
                variant="outline"
                className="h-auto max-w-full rounded-md text-left font-normal break-words whitespace-normal"
              >
                {s}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">No synonyms in the vocabulary.</p>
        )}
      </Section>

      {(c.atc7_codes ?? []).length > 0 && (
        <Section title="ATC7 codes">
          <div className="flex flex-wrap gap-1.5">
            {(c.atc7_codes ?? []).map((code) => (
              <Badge key={code} variant="secondary" className="font-mono">
                {code}
              </Badge>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function Explanation({ conceptId, onClose }: { conceptId: number; onClose: () => void }) {
  // Mounted only when the user asked for it, so the query runs right away
  const explanation = useConceptExplanation(conceptId, true);
  const data = explanation.data;

  return (
    <div className="from-brand-from/[0.06] to-brand-to/[0.08] relative overflow-hidden rounded-xl border bg-gradient-to-br p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Sparkles className="text-primary size-4" /> AI explanation
        </div>
        <div className="flex items-center gap-1">
          {explanation.isError && (
            <Button variant="outline" size="sm" onClick={() => void explanation.refetch()}>
              Try again
            </Button>
          )}
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Hide the AI explanation">
            <X />
          </Button>
        </div>
      </div>
      <AnimatePresence mode="wait">
        {explanation.isFetching && !data ? (
          <motion.div
            key="loading"
            className="mt-3 space-y-2"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div className="text-primary flex items-center gap-2 text-xs font-medium">
              <motion.span
                animate={{ rotate: [0, 15, -15, 0], scale: [1, 1.15, 1] }}
                transition={{ duration: 1.4, repeat: Infinity }}
              >
                <Sparkles className="size-3.5" />
              </motion.span>
              Reading the vocabulary data…
            </div>
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-11/12" />
            <Skeleton className="h-3.5 w-4/5" />
            <Skeleton className="h-3.5 w-2/3" />
          </motion.div>
        ) : explanation.isError ? (
          <motion.p
            key="error"
            className="text-destructive mt-2 text-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            {errorMessage(explanation.error)}
          </motion.p>
        ) : data ? (
          <motion.div
            key="text"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
          >
            <div className="mt-3 space-y-2 text-sm leading-relaxed">
              {data.explanation
                .split(/\n+/)
                .filter((line) => line.trim())
                .map((line, i) => {
                  const m = line.match(/^([A-Z][^:]{2,40}):\s*(.*)$/);
                  return (
                    <p key={i}>
                      {m ? (
                        <>
                          <span className="font-semibold">{m[1]}:</span> {m[2]}
                        </>
                      ) : (
                        line
                      )}
                    </p>
                  );
                })}
            </div>
            <p className="text-muted-foreground mt-3 text-[11px]">
              Generated by {data.model}
              {data.cached ? " (saved earlier)" : ""}. AI text can be wrong: check it against the vocabulary data.
            </p>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-muted-foreground mb-2 text-xs font-semibold tracking-wider uppercase">{title}</div>
      {children}
    </div>
  );
}

function ConceptRow({ concept, onOpen, note }: { concept: Concept; onOpen: (id: number) => void; note?: string }) {
  const std = standardLabel(concept);
  return (
    <motion.button
      type="button"
      layout="position"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      onClick={() => onOpen(concept.concept_id)}
      className="group hover:bg-accent/60 flex w-full cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors"
    >
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{concept.concept_name}</div>
        <div className="text-muted-foreground truncate text-xs">
          {note ? `${note} · ` : ""}
          {concept.concept_class_id} · {concept.vocabulary_id} · {concept.concept_id}
          {concept.standard_concept !== "S" ? ` · ${std.text.toLowerCase()}` : ""}
        </div>
      </div>
      <ChevronRight className="text-muted-foreground size-4 shrink-0 transition-transform group-hover:translate-x-0.5" />
    </motion.button>
  );
}

function ConceptList({ items, onOpen, empty }: { items: Concept[]; onOpen: (id: number) => void; empty?: string }) {
  if (!items.length) return <p className="text-muted-foreground px-3 text-sm">{empty ?? "None."}</p>;
  return (
    <div className="divide-border/60 -mx-1 divide-y">
      {items.map((c) => (
        <ConceptRow key={c.concept_id} concept={c} onOpen={onOpen} />
      ))}
    </div>
  );
}

function Hierarchy({ details, onOpen }: { details: ConceptDetails; onOpen: (id: number) => void }) {
  const more = (shown: number, total: number) =>
    total > shown ? <p className="text-muted-foreground px-3 pt-1 text-xs">… and {total - shown} more</p> : null;
  return (
    <div className="space-y-3">
      <Section title={`Parents · more general (${details.parent_count})`}>
        <ConceptList items={details.parents} onOpen={onOpen} empty="No parents: this is a top-level concept." />
        {more(details.parents.length, details.parent_count)}
      </Section>
      <div className="flex items-center gap-2 py-1 pl-3">
        <ArrowUp className="text-muted-foreground size-3.5" />
        <div className="bg-brand rounded-lg px-3 py-1.5 text-sm font-medium text-white shadow-[var(--shadow-glow)]">
          {details.concept.concept_name}
        </div>
        <ArrowDown className="text-muted-foreground size-3.5" />
      </div>
      <Section title={`Children · more specific (${details.child_count})`}>
        <ConceptList items={details.children} onOpen={onOpen} empty="No children: nothing is more specific." />
        {more(details.children.length, details.child_count)}
      </Section>
      <p className="text-muted-foreground pt-2 text-xs">Click a concept to open it; use ← to go back.</p>
    </div>
  );
}

function Related({ related, onOpen }: { related: RelatedConcept[]; onOpen: (id: number) => void }) {
  const groups = useMemo(() => {
    const by = new Map<string, RelatedConcept[]>();
    for (const r of related) by.set(r.relationship_id, [...(by.get(r.relationship_id) ?? []), r]);
    return [...by.entries()];
  }, [related]);
  if (!groups.length)
    return <p className="text-muted-foreground text-sm">No other relationships (the hierarchy is under Hierarchy).</p>;
  return (
    <div className="space-y-4">
      {groups.map(([relationship, items]) => (
        <Section key={relationship} title={`${relationship} (${items.length})`}>
          <ConceptList items={items} onOpen={onOpen} />
        </Section>
      ))}
    </div>
  );
}
