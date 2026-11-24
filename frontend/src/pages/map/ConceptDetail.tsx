import type { ColumnDef } from "@tanstack/react-table";
import { Filter, Hash, Loader2, RotateCcw, Search as SearchIcon, Sparkles } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ApiError, errorMessage } from "@/api/client";
import {
  useDecideSuggestions,
  useDrugMatch,
  useMapConcepts,
  useSearch,
  useSuggest,
  useSuggestProgress,
  useSuggestions,
  useUnmap,
} from "@/api/queries";
import type { DrugStructure, SearchRequest, SuggestResponse, UnmappedItem } from "@/api/types";
import { DataTable } from "@/components/DataTable";
import { ErrorAlert } from "@/components/common";
import { ConceptFilterFields } from "@/components/ConceptFilterFields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Slider } from "@/components/ui/slider";
import { useAutomapThreshold } from "@/hooks/useAutomapThreshold";
import { useDomainMode } from "@/hooks/useDomainMode";
import { useDebounce } from "@/hooks/useDebounce";
import { usePersistentState } from "@/hooks/usePersistentState";
import { isValidAtc7 } from "@/lib/atc7";
import { ManualConceptIds } from "./ManualConceptIds";
import { DrugPanel } from "./DrugPanel";
import { DEFAULT_PIPELINE, PipelineSelect } from "./PipelineSelect";
import { SelectedConcepts } from "./SelectedConcepts";
import { SuggestionAlert } from "./SuggestionAlert";
import { BelowThresholdPick, FlagBanner, FlagButton, WaitingSuggestion } from "./TermExtras";
import { DEFAULT_LIMIT } from "./searchRequest";
import type { ConceptLike } from "./types";
import { ConceptLink } from "@/components/ConceptLink";
import { UserAvatar } from "@/components/UserAvatar";
import { useConceptModal } from "@/hooks/useConceptModal";
import { cn } from "@/lib/utils";

function ExplainButton({ conceptId }: { conceptId: number }) {
  const { openConcept } = useConceptModal();
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className="text-primary"
      aria-label="Explain with AI"
      title="Details and AI explanation"
      onClick={(e) => {
        e.stopPropagation();
        openConcept(conceptId, { explain: true });
      }}
    >
      <Sparkles />
    </Button>
  );
}

/** Seconds since mount, for long waits such as AI suggest (which may ask a stronger model). */
/** A random id for polling AI suggest progress (crypto.randomUUID needs HTTPS or localhost). */
function newProgressId(set: (id: string) => void): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const id = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  set(id);
  return id;
}

function Elapsed({ prefix }: { prefix: string }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="tabular-nums">
      {prefix}
      {seconds}s
    </span>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="bg-muted text-foreground mx-px rounded border px-1 py-px font-mono text-[10px] font-medium">
      {children}
    </kbd>
  );
}

const RESULT_COLUMNS: ColumnDef<ConceptLike, unknown>[] = [
  {
    id: "key",
    header: "#",
    size: 28,
    enableSorting: false,
    cell: ({ row }) =>
      row.index < 9 ? <span className="text-muted-foreground font-mono text-xs">{row.index + 1}</span> : null,
  },
  {
    accessorKey: "concept_name",
    header: "Standard concept",
    cell: ({ row }) => {
      const c = row.original;
      const atc7 = c.atc7_codes ?? [];
      return (
        <div className="min-w-0">
          <ConceptLink conceptId={c.concept_id} className="leading-snug font-medium">
            {c.concept_name}
          </ConceptLink>
          {c.match && (
            <Badge
              variant="outline"
              className={cn(
                "ml-1.5 align-middle text-[10px]",
                c.match === "approximate"
                  ? "border-warning/40 bg-warning/10 text-warning"
                  : "border-success/40 bg-success/10 text-success",
              )}
              title="Found by matching the drug structure (ingredients, strengths, form) on drug_strength"
            >
              {c.match === "exact"
                ? "structure match"
                : c.match === "tie"
                  ? "structure match (one of several)"
                  : "close structure match"}
            </Badge>
          )}
          <div className="text-muted-foreground text-xs">
            {c.concept_class_id} · {c.vocabulary_id} · {c.concept_id}
            {atc7.length > 0 && <span className="font-mono"> · {atc7.slice(0, 2).join(", ")}</span>}
          </div>
        </div>
      );
    },
  },
  { accessorKey: "domain_id", header: "Domain", size: 110 },
  {
    accessorKey: "score",
    header: "Score",
    size: 70,
    cell: ({ row }) =>
      row.original.match ? (
        <span className="text-muted-foreground text-xs">match</span>
      ) : (
        <span className="text-xs tabular-nums">{row.original.score?.toFixed(3) ?? "—"}</span>
      ),
  },
  {
    id: "explain",
    header: "",
    size: 44,
    enableSorting: false,
    cell: ({ row }) => <ExplainButton conceptId={row.original.concept_id} />,
  },
];

type Props = {
  concept: UnmappedItem;
  vocabularyId: number;
  isDrug: boolean;
  onMapped: () => void;
  /** Called after "Undo" removed the mapping just confirmed, with the source term's ID */
  onUndone: (sourceId: number) => void;
  /** Colleagues who have this term open right now */
  othersHere: string[];
};

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

export function ConceptDetail({ concept, vocabularyId, isDrug, onMapped, onUndone, othersHere }: Props) {
  const [name, setName] = useState(concept.source_concept_name);
  const [domains, setDomains] = usePersistentState<string[]>("map-domains", []);
  const [vocabularies, setVocabularies] = useState<string[]>([]);
  const [conceptClasses, setConceptClasses] = useState<string[]>([]);
  const detected = concept.atc7_codes?.[0] ?? null;
  const [useAtc7, setUseAtc7] = useState(isDrug && !!detected);
  const [manualAtc7, setManualAtc7] = useState("");
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [pipeline, setPipeline] = useState(DEFAULT_PIPELINE);
  const [selected, setSelected] = useState<Map<number, ConceptLike>>(new Map());
  const [suggestion, setSuggestion] = useState<SuggestResponse | null>(null);
  const [threshold] = useAutomapThreshold();
  const [domainMode] = useDomainMode();
  // Drugs: the structure as auto-mapping reads it (from the CSV, or the name read by the AI),
  // which the user can edit; its matches are listed first and it shapes the search below
  const [editedStructure, setEditedStructure] = useState<DrugStructure | null>(null);
  const [showBranded, setShowBranded] = useState(false);
  const drugMatch = useDrugMatch(concept.source_id, editedStructure, isDrug);
  const dm = isDrug ? drugMatch.data : undefined;
  // No reset effect: the parent keys this component by source_id, so state starts fresh per concept

  const manualValid = manualAtc7 === "" || isValidAtc7(manualAtc7);
  const atc7Codes = useMemo(() => {
    if (!isDrug || !useAtc7) return [];
    if (detected) return [detected];
    return manualAtc7 && isValidAtc7(manualAtc7) ? [manualAtc7] : [];
  }, [isDrug, useAtc7, detected, manualAtc7]);

  const edited = name.trim() !== concept.source_concept_name.trim();
  const debouncedName = useDebounce(name.trim(), 400);
  const debouncedLimit = useDebounce(limit, 400);
  // A provided structure is matched quickly (no AI): wait for it, so the first search is already
  // the drug search. A name the AI must read is searched as typed meanwhile.
  const waitForDrug = isDrug && !!concept.drug_structure && drugMatch.isPending;
  const request: SearchRequest | null =
    debouncedName && !waitForDrug
      ? {
          query: debouncedName,
          k: debouncedLimit,
          domains,
          vocabularies,
          concept_classes: conceptClasses,
          atc7_codes: atc7Codes,
          // As auto-mapping: also search the structure written like an RxNorm name, leave
          // branded drugs out when the term names no brand, and drop a fruitless ATC7 filter.
          // Text the user typed is searched as typed (it may be a brand name).
          extra_queries: !edited && dm?.search_text ? [dm.search_text] : [],
          exclude_concept_classes: edited || showBranded ? [] : (dm?.exclude_classes ?? []),
          atc7_fallback: isDrug,
        }
      : null;
  const search = useSearch(request);
  const results = useMemo(() => {
    const found = (search.data?.results ?? []) as ConceptLike[];
    const status = dm?.status;
    if (edited || !dm?.candidates.length || (status !== "exact" && status !== "tie" && status !== "approximate"))
      return found;
    // Structured matches first, as auto-mapping gives them to the AI first
    const ids = new Set(dm.candidates.map((c) => c.concept_id));
    return [...dm.candidates.map((c) => ({ ...c, match: status })), ...found.filter((r) => !ids.has(r.concept_id))];
  }, [search.data, dm, edited]);
  const drugIds = (dm?.candidates ?? []).map((c) => c.concept_id);
  const drugUsed = drugIds.length > 0 && drugIds.every((id) => selected.has(id));

  const suggest = useSuggest();
  // Polled while AI suggest runs, to show which step it is at
  const [progressId, setProgressId] = useState<string | null>(null);
  const progress = useSuggestProgress(suggest.isPending ? progressId : null);
  const step = suggest.isPending ? (progress.data?.step ?? null) : null;
  const mapConcepts = useMapConcepts(vocabularyId);
  const unmap = useUnmap(vocabularyId);
  const acceptSuggestion = useDecideSuggestions(vocabularyId, "accept");
  const rejectSuggestion = useDecideSuggestions(vocabularyId, "reject");

  // An AI suggestion already waiting for this term (auto-mapping in review mode): shown here and
  // pre-ticked, so accepting it takes one key
  const waitingQuery = useSuggestions(concept.suggestion_id ? vocabularyId : null, {
    status: "pending",
    source_id: concept.source_id,
    page: 1,
    per_page: 1,
    sort: "newest",
  });
  const waiting = concept.suggestion_id ? (waitingQuery.data?.items[0] ?? null) : null;
  // The latest auto-mapping pick under the threshold (not queued): shown as a hint, not pre-ticked
  const lowQuery = useSuggestions(concept.low_concept_id && !concept.suggestion_id ? vocabularyId : null, {
    status: "below_threshold",
    source_id: concept.source_id,
    page: 1,
    per_page: 1,
    sort: "newest",
  });
  const lowPick = concept.low_concept_id && !concept.suggestion_id ? (lowQuery.data?.items[0] ?? null) : null;
  const waitingIds = useMemo(
    () => (waiting ? [waiting.concept_id, ...(waiting.extra_concepts ?? []).map((c) => c.concept_id)] : []),
    [waiting],
  );
  const preticked = useRef(false);
  useEffect(() => {
    if (!waiting || preticked.current) return;
    preticked.current = true;
    const picks: ConceptLike[] = [
      {
        concept_id: waiting.concept_id,
        concept_name: waiting.concept_name,
        domain_id: waiting.domain_id,
        vocabulary_id: waiting.vocabulary_id,
        concept_class_id: waiting.concept_class_id,
      },
      ...(waiting.extra_concepts ?? []).map((c) => ({
        concept_id: c.concept_id,
        concept_name: c.concept_name,
        domain_id: waiting.domain_id,
        vocabulary_id: waiting.vocabulary_id,
        concept_class_id: c.concept_class_id,
      })),
    ];
    setSelected((prev) => (prev.size ? prev : new Map(picks.map((c) => [c.concept_id, c]))));
  }, [waiting]);

  const addConcept = (c: ConceptLike) => setSelected((prev) => new Map(prev).set(c.concept_id, c));
  const removeConcept = (id: number) =>
    setSelected((prev) => {
      const next = new Map(prev);
      next.delete(id);
      return next;
    });

  // Selection in the results table mirrors the selected map (for concepts present in the results)
  const selectedIds = [...selected.keys()].map(String);
  const onTableSelection = (ids: string[]) => {
    const idSet = new Set(ids.map(Number));
    setSelected((prev) => {
      const next = new Map(prev);
      for (const r of results) {
        if (idSet.has(r.concept_id)) next.set(r.concept_id, r);
        else next.delete(r.concept_id);
      }
      return next;
    });
  };

  const runSuggest = () =>
    suggest.mutate(
      {
        progress: newProgressId(setProgressId),
        sourceId: concept.source_id,
        query: name.trim() !== concept.source_concept_name ? name.trim() : null,
        domains,
        domain_mode: domainMode,
        drug_specific: isDrug,
        atc7_codes: atc7Codes.length ? atc7Codes : null,
        threshold,
        pipeline: pipeline === DEFAULT_PIPELINE ? null : pipeline,
      },
      { onSuccess: setSuggestion, onError: (err) => toast.error(errorMessage(err)) },
    );

  const decideWaiting = (kind: "accept" | "reject") => {
    if (!waiting) return;
    (kind === "accept" ? acceptSuggestion : rejectSuggestion).mutate(
      { ids: [waiting.id] },
      {
        onSuccess: () => {
          if (kind === "accept") {
            toast.success(`"${concept.source_concept_name}" mapped (AI suggestion accepted)`);
            onMapped();
          } else {
            toast.message("Suggestion rejected: pick the right concept below");
            setSelected(new Map());
          }
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  };

  const confirm = () => {
    // Confirming exactly the waiting suggestion records it as accepted (keeps the calibration)
    const ids = [...selected.keys()].sort();
    if (waiting && ids.length === waitingIds.length && [...waitingIds].sort().every((id, i) => id === ids[i])) {
      decideWaiting("accept");
      return;
    }
    mapConcepts.mutate(
      { source_id: concept.source_id, concept_ids: [...selected.keys()] },
      {
        onSuccess: (res) => {
          const sourceId = concept.source_id;
          toast.success(`"${concept.source_concept_name}" mapped to ${res.count} concept(s)`, {
            duration: 8000,
            action: {
              label: "Undo",
              onClick: () =>
                unmap.mutate([sourceId], {
                  onSuccess: () => {
                    toast.message("Mapping undone");
                    onUndone(sourceId);
                  },
                  onError: (err) => toast.error(errorMessage(err)),
                }),
            },
          });
          setSelected(new Map());
          onMapped();
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  };

  // Shortcuts: 1-9 tick a candidate, Enter confirms, S asks the AI (not while typing or in a dialog)
  const shortcuts = useRef({ results, selected, confirm, runSuggest, toggle: onTableSelection, selectedIds });
  useEffect(() => {
    shortcuts.current = { results, selected, confirm, runSuggest, toggle: onTableSelection, selectedIds };
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target) || document.querySelector('[role="dialog"]')) return;
      const k = shortcuts.current;
      if (/^[1-9]$/.test(e.key)) {
        const c = k.results[Number(e.key) - 1];
        if (!c) return;
        e.preventDefault();
        const id = String(c.concept_id);
        k.toggle(k.selectedIds.includes(id) ? k.selectedIds.filter((x) => x !== id) : [...k.selectedIds, id]);
      } else if (e.key === "Enter" && k.selected.size > 0) {
        // Enter on a focused row or button keeps its own meaning
        const tag = (e.target as HTMLElement | null)?.tagName;
        if (tag === "TR" || tag === "BUTTON" || tag === "A") return;
        e.preventDefault();
        k.confirm();
      } else if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        k.runSuggest();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const searchError =
    search.error instanceof ApiError && search.error.status === 409
      ? "No embeddings exist for the current embedding model yet. Embed standard concepts first (Import Data → Embedding Management)."
      : search.error;

  const suggested = suggestion?.selected ?? null;

  const filterCount = domains.length + vocabularies.length + conceptClasses.length + (atc7Codes.length ? 1 : 0);
  const typing = name.trim() !== debouncedName;
  const busy = typing || search.isFetching;

  return (
    <Card className="h-full min-h-[36rem] gap-0 py-0">
      <CardContent className="flex h-full min-h-0 flex-col gap-3 p-4">
        {/* The term being mapped */}
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
          className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1"
        >
          <div className="min-w-0">
            <div className="text-muted-foreground text-xs font-medium tracking-wide uppercase">Mapping</div>
            <h2 className="text-lg leading-snug font-semibold">{concept.source_concept_name}</h2>
          </div>
          <div className="text-muted-foreground flex flex-wrap gap-x-4 pt-4 text-xs">
            <span>
              Code <b className="text-foreground font-mono">{concept.source_value}</b>
            </span>
            <span>
              Frequency <b className="text-foreground tabular-nums">{concept.freq?.toLocaleString() ?? "—"}</b>
            </span>
            {detected && (
              <span>
                ATC7 <b className="text-foreground font-mono">{detected}</b>
              </span>
            )}
          </div>
        </motion.div>

        {isDrug && (
          <DrugPanel
            match={drugMatch.data}
            loading={drugMatch.isFetching}
            error={drugMatch.error}
            edited={editedStructure}
            onEdit={setEditedStructure}
            showBranded={showBranded}
            onShowBranded={setShowBranded}
            used={drugUsed}
            searchEdited={edited}
            onUse={() => {
              for (const c of dm?.candidates ?? [])
                addConcept({ ...c, match: dm?.status === "exact" ? "exact" : undefined });
            }}
          />
        )}

        {/* Search and AI. The box starts with the source term; first-time users need to be told
            they can change it */}
        <div className="grid gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="concept-search">Search standard concepts</Label>
            {edited && (
              <button
                type="button"
                className="text-primary flex cursor-pointer items-center gap-1 text-xs hover:underline"
                onClick={() => setName(concept.source_concept_name)}
              >
                <RotateCcw className="size-3" /> Back to the source term
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-56 flex-1">
              <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
              <Input
                id="concept-search"
                aria-describedby="concept-search-hint"
                placeholder="Type words to search, e.g. type 2 diabetes mellitus"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="pr-9 pl-9"
              />
              {busy && (
                <Loader2
                  aria-label="Searching"
                  className="text-primary absolute top-1/2 right-3 size-4 -translate-y-1/2 animate-spin"
                />
              )}
            </div>
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" className="gap-2">
                  <Filter className="size-4" /> Filters
                  {filterCount > 0 && <Badge variant="secondary">{filterCount}</Badge>}
                </Button>
              </PopoverTrigger>
              <PopoverContent
                align="end"
                side="bottom"
                collisionPadding={12}
                className="max-h-(--radix-popover-content-available-height) w-96 space-y-4 overflow-y-auto"
              >
                <ConceptFilterFields
                  idPrefix="map-filter"
                  values={{ domains, vocabularies, conceptClasses }}
                  onChange={(next) => {
                    setDomains(next.domains);
                    setVocabularies(next.vocabularies);
                    setConceptClasses(next.conceptClasses);
                  }}
                />
                <p className="text-muted-foreground -mt-2 text-xs">AI suggest uses the domain filter only.</p>
                {isDrug && (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <Checkbox id="use-atc7" checked={useAtc7} onCheckedChange={(v) => setUseAtc7(!!v)} />
                      <Label htmlFor="use-atc7">Enable ATC7 filtering</Label>
                    </div>
                    {useAtc7 &&
                      (detected ? (
                        <p className="text-sm">
                          Using ATC7 code from source value: <b className="font-mono">{detected}</b>
                        </p>
                      ) : (
                        <div className="grid gap-1.5">
                          <p className="text-muted-foreground text-xs">
                            No ATC7 code detected at the beginning of the source value.
                          </p>
                          <Input
                            aria-label="ATC7 code"
                            placeholder="A10BA02"
                            maxLength={7}
                            value={manualAtc7}
                            onChange={(e) => setManualAtc7(e.target.value.toUpperCase().trim())}
                          />
                          {!manualValid && manualAtc7.length > 0 && (
                            <p className="text-destructive text-xs">
                              Invalid ATC7 code format. Expected: Letter-Digit-Digit-Letter-Letter-Digit-Digit (e.g.,
                              A10BA02)
                            </p>
                          )}
                        </div>
                      ))}
                  </div>
                )}
                <div className="grid gap-2">
                  <Label>
                    Max results: <span className="tabular-nums">{limit}</span>
                  </Label>
                  <Slider
                    thumbLabel="Max results"
                    min={10}
                    max={200}
                    step={10}
                    value={[limit]}
                    onValueChange={(v) => setLimit(v[0] ?? 100)}
                  />
                </div>
                <div className="text-muted-foreground text-xs">
                  AI suggest uses the confidence threshold from Auto map settings ({threshold}).
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="suggest-pipeline">AI suggest pipeline</Label>
                  <PipelineSelect id="suggest-pipeline" value={pipeline} onChange={setPipeline} />
                </div>
              </PopoverContent>
            </Popover>
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" className="gap-2">
                  <Hash className="size-4" /> By ID
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-96">
                <ManualConceptIds isSelected={(id) => selected.has(id)} onAdd={addConcept} alwaysOpen />
              </PopoverContent>
            </Popover>
            <Button variant="secondary" className="gap-2" onClick={runSuggest} disabled={suggest.isPending}>
              {suggest.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
              {suggest.isPending ? <Elapsed prefix="Thinking… " /> : "AI suggest"}
            </Button>
            {!concept.flag && <FlagButton concept={concept} vocabularyId={vocabularyId} onFlagged={onMapped} />}
          </div>
          <p id="concept-search-hint" className="text-muted-foreground text-xs">
            {edited
              ? "Searching for your text instead of the source term; AI suggest uses it too."
              : "Starts with the source term. No good match? Edit it: fix a typo, write it in English, or try other words. Results update as you type."}
          </p>
          {suggest.isPending && (
            <div aria-live="polite" className="text-primary flex min-w-0 items-center gap-2 text-sm">
              <Loader2 className="size-4 shrink-0 animate-spin" />
              <span className="truncate">AI suggest: {step ?? "starting"}…</span>
            </div>
          )}
        </div>

        {othersHere.length > 0 && (
          <div className="border-warning/40 bg-warning/10 flex items-center gap-2 rounded-xl border px-3 py-2 text-sm">
            <div className="flex -space-x-1.5">
              {othersHere.map((name) => (
                <UserAvatar key={name} name={name} />
              ))}
            </div>
            <span>
              <b>{othersHere.join(", ")}</b> {othersHere.length === 1 ? "has" : "have"} this term open too. Agree on who
              maps it, or pick another term.
            </span>
          </div>
        )}
        <FlagBanner concept={concept} vocabularyId={vocabularyId} />
        {waiting && !suggestion && (
          <WaitingSuggestion
            suggestion={waiting}
            busy={acceptSuggestion.isPending || rejectSuggestion.isPending}
            onAccept={() => decideWaiting("accept")}
            onReject={() => decideWaiting("reject")}
          />
        )}

        {lowPick && !suggestion && (
          <BelowThresholdPick
            suggestion={lowPick}
            onSelect={() => {
              addConcept({
                concept_id: lowPick.concept_id,
                concept_name: lowPick.concept_name,
                domain_id: lowPick.domain_id,
                vocabulary_id: lowPick.vocabulary_id,
                concept_class_id: lowPick.concept_class_id,
              });
              for (const c of lowPick.extra_concepts ?? [])
                addConcept({
                  concept_id: c.concept_id,
                  concept_name: c.concept_name,
                  domain_id: lowPick.domain_id,
                  vocabulary_id: lowPick.vocabulary_id,
                  concept_class_id: c.concept_class_id,
                });
            }}
          />
        )}

        <AnimatePresence initial={false}>
          {suggestion && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="shrink-0 overflow-hidden"
            >
              <SuggestionAlert suggestion={suggestion} threshold={threshold} onSelect={addConcept} />
            </motion.div>
          )}
        </AnimatePresence>

        <SelectedConcepts
          concepts={[...selected.values()]}
          onRemove={removeConcept}
          onConfirm={confirm}
          confirming={mapConcepts.isPending || acceptSuggestion.isPending}
        />

        <ErrorAlert error={searchError} title="Search failed" />

        {/* Candidates: a fixed area that keeps the previous results under a loading bar */}
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div className="text-muted-foreground flex items-center justify-between text-xs">
            <span>
              {search.data ? `${results.length} candidates` : "Searching…"}
              <span className="hidden sm:inline">
                {" "}
                · <Kbd>1</Kbd>–<Kbd>9</Kbd> tick · <Kbd>Enter</Kbd> confirm · <Kbd>S</Kbd> AI suggest · <Kbd>↑</Kbd>
                <Kbd>↓</Kbd> term
              </span>
            </span>
            {search.data && <span>{search.data.method}</span>}
          </div>
          <div className="relative min-h-0 flex-1">
            {busy && search.data && (
              <div className="bg-primary/15 absolute inset-x-0 top-0 z-20 h-0.5 overflow-hidden rounded-full">
                <motion.div
                  className="bg-brand h-full w-1/3 rounded-full"
                  initial={{ x: "-100%" }}
                  animate={{ x: "300%" }}
                  transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
                />
              </div>
            )}
            <DataTable
              columns={RESULT_COLUMNS}
              data={results}
              getRowId={(r) => String(r.concept_id)}
              selection="multi"
              selected={selectedIds}
              onSelectedChange={onTableSelection}
              highlightId={suggested ? String(suggested.concept_id) : null}
              isLoading={!search.data && (search.isLoading || typing)}
              emptyText="No similar concepts found. Try other words or fewer filters."
              maxHeight="none"
              className={cn("h-[28rem] transition-opacity xl:h-full", busy && search.data && "opacity-60")}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
