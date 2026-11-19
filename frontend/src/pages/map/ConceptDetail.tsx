import type { ColumnDef } from "@tanstack/react-table";
import { Filter, Hash, Loader2, Search as SearchIcon, Sparkles } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { ApiError, errorMessage } from "@/api/client";
import { useMapConcepts, useMeta, useSearch, useSuggest } from "@/api/queries";
import type { SearchRequest, SuggestResponse, UnmappedItem } from "@/api/types";
import { DataTable } from "@/components/DataTable";
import { ErrorAlert } from "@/components/common";
import { MultiSelect } from "@/components/MultiSelect";
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
import { DEFAULT_PIPELINE, PipelineSelect } from "./PipelineSelect";
import { SelectedConcepts } from "./SelectedConcepts";
import { SuggestionAlert } from "./SuggestionAlert";
import type { ConceptLike } from "./types";
import { ConceptLink } from "@/components/ConceptLink";
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

const RESULT_COLUMNS: ColumnDef<ConceptLike, unknown>[] = [
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
    cell: ({ row }) => <span className="text-xs tabular-nums">{row.original.score?.toFixed(3) ?? "—"}</span>,
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
};

export function ConceptDetail({ concept, vocabularyId, isDrug, onMapped }: Props) {
  const meta = useMeta();
  const [name, setName] = useState(concept.source_concept_name);
  const [domains, setDomains] = usePersistentState<string[]>("map-domains", []);
  const [vocabFilter, setVocabFilter] = useState("");
  const detected = concept.atc7_codes?.[0] ?? null;
  const [useAtc7, setUseAtc7] = useState(isDrug && !!detected);
  const [manualAtc7, setManualAtc7] = useState("");
  const [limit, setLimit] = useState(100);
  const [pipeline, setPipeline] = useState(DEFAULT_PIPELINE);
  const [selected, setSelected] = useState<Map<number, ConceptLike>>(new Map());
  const [suggestion, setSuggestion] = useState<SuggestResponse | null>(null);
  const [threshold] = useAutomapThreshold();
  const [domainMode] = useDomainMode();
  // No reset effect: the parent keys this component by source_id, so state starts fresh per concept

  const manualValid = manualAtc7 === "" || isValidAtc7(manualAtc7);
  const atc7Codes = useMemo(() => {
    if (!isDrug || !useAtc7) return [];
    if (detected) return [detected];
    return manualAtc7 && isValidAtc7(manualAtc7) ? [manualAtc7] : [];
  }, [isDrug, useAtc7, detected, manualAtc7]);

  const debouncedName = useDebounce(name.trim(), 400);
  const debouncedVocab = useDebounce(vocabFilter.trim(), 400);
  const debouncedLimit = useDebounce(limit, 400);
  const request: SearchRequest | null = debouncedName
    ? { query: debouncedName, k: debouncedLimit, domains, vocabulary_id: debouncedVocab || null, atc7_codes: atc7Codes }
    : null;
  const search = useSearch(request);
  const results = (search.data?.results ?? []) as ConceptLike[];

  const suggest = useSuggest();
  const mapConcepts = useMapConcepts(vocabularyId);

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

  const confirm = () =>
    mapConcepts.mutate(
      { source_id: concept.source_id, concept_ids: [...selected.keys()] },
      {
        onSuccess: (res) => {
          toast.success(`Mapped to ${res.count} concept(s)`);
          setSelected(new Map());
          onMapped();
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );

  const searchError =
    search.error instanceof ApiError && search.error.status === 409
      ? "No embeddings exist for the current embedding model yet. Embed standard concepts first (Import Data → Embedding Management)."
      : search.error;

  const suggested = suggestion?.selected ?? null;

  const filterCount = domains.length + (vocabFilter ? 1 : 0) + (atc7Codes.length ? 1 : 0);
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

        {/* Search and AI */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-56 flex-1">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
            <Input
              aria-label="Search text"
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
              <div className="grid gap-1.5">
                <Label htmlFor="map-domains">Target domains</Label>
                <MultiSelect
                  id="map-domains"
                  options={meta.data?.domains ?? []}
                  value={domains}
                  onChange={setDomains}
                  placeholder="All domains"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="vocab-filter">Vocabulary ID</Label>
                <Input
                  id="vocab-filter"
                  placeholder="e.g. SNOMED, RxNorm"
                  value={vocabFilter}
                  onChange={(e) => setVocabFilter(e.target.value)}
                />
              </div>
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
            {suggest.isPending ? "Thinking…" : "AI suggest"}
          </Button>
        </div>

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
          confirming={mapConcepts.isPending}
        />

        <ErrorAlert error={searchError} title="Search failed" />

        {/* Candidates: a fixed area that keeps the previous results under a loading bar */}
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div className="text-muted-foreground flex items-center justify-between text-xs">
            <span>{search.data ? `${results.length} candidates · tick to select` : "Searching…"}</span>
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
