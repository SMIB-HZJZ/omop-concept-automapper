import type { ColumnDef } from "@tanstack/react-table";
import { Filter, Search as SearchIcon, Sparkles } from "lucide-react";
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Slider } from "@/components/ui/slider";
import { useAutomapThreshold } from "@/hooks/useAutomapThreshold";
import { useDebounce } from "@/hooks/useDebounce";
import { usePersistentState } from "@/hooks/usePersistentState";
import { isValidAtc7 } from "@/lib/atc7";
import { ManualConceptIds } from "./ManualConceptIds";
import { DEFAULT_PIPELINE, PipelineSelect } from "./PipelineSelect";
import { SelectedConcepts } from "./SelectedConcepts";
import { SuggestionAlert } from "./SuggestionAlert";
import type { ConceptLike } from "./types";

const RESULT_COLUMNS: ColumnDef<ConceptLike, unknown>[] = [
  {
    accessorKey: "score",
    header: "Similarity",
    cell: ({ row }) => <span className="tabular-nums">{row.original.score?.toFixed(3) ?? "—"}</span>,
  },
  { accessorKey: "concept_name", header: "Concept name" },
  { accessorKey: "concept_id", header: "ID" },
  { accessorKey: "domain_id", header: "Domain" },
  { accessorKey: "concept_class_id", header: "Class" },
  { accessorKey: "vocabulary_id", header: "Vocabulary" },
  { accessorKey: "concept_code", header: "Code" },
  {
    id: "atc7",
    header: "ATC7",
    enableSorting: false,
    cell: ({ row }) => {
      const codes = row.original.atc7_codes ?? [];
      return codes.length ? (
        <span className="font-mono text-xs">
          {codes.slice(0, 3).join(", ")}
          {codes.length > 3 ? ` +${codes.length - 3}` : ""}
        </span>
      ) : null;
    },
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

  return (
    <Card>
      <CardHeader>
        <CardTitle>Mapping: {concept.source_concept_name}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-4">
          <div className="grid min-w-64 flex-1 gap-1.5">
            <Label htmlFor="search-name">Source concept name</Label>
            <Input id="search-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="text-sm">
            <span className="text-muted-foreground">Source ID</span> <b>{concept.source_id}</b>
          </div>
          <div className="text-sm">
            <span className="text-muted-foreground">Frequency</span> <b>{concept.freq ?? "—"}</b>
          </div>
          <div className="text-sm">
            <span className="text-muted-foreground">Source value</span>{" "}
            <b className="font-mono">{concept.source_value}</b>
          </div>
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" className="gap-2">
                <Filter className="size-4" /> Search filters
                {domains.length + (vocabFilter ? 1 : 0) + (atc7Codes.length ? 1 : 0) > 0 && (
                  <Badge variant="secondary">
                    {domains.length + (vocabFilter ? 1 : 0) + (atc7Codes.length ? 1 : 0)}
                  </Badge>
                )}
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
          <Button className="gap-2" onClick={() => void search.refetch()} disabled={!request}>
            <SearchIcon className="size-4" /> Search
          </Button>
          <Button variant="secondary" className="gap-2" onClick={runSuggest} disabled={suggest.isPending}>
            <Sparkles className="size-4" /> {suggest.isPending ? "Thinking…" : "AI suggest"}
          </Button>
        </div>

        {suggestion && <SuggestionAlert suggestion={suggestion} threshold={threshold} onSelect={addConcept} />}

        <ErrorAlert error={searchError} title="Search failed" />

        <div className="grid gap-4 xl:grid-cols-[3fr_1fr]">
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span>{search.isFetching ? "Searching…" : `Found ${results.length} similar concepts`}</span>
              {search.data && (
                <span className="text-muted-foreground text-xs">Search method: {search.data.method}</span>
              )}
            </div>
            <DataTable
              columns={RESULT_COLUMNS}
              data={results}
              getRowId={(r) => String(r.concept_id)}
              selection="multi"
              selected={selectedIds}
              onSelectedChange={onTableSelection}
              highlightId={suggested ? String(suggested.concept_id) : null}
              isLoading={search.isLoading}
              emptyText="No similar concepts found. Try adjusting your search term or filters."
              maxHeight="28rem"
            />
            <ManualConceptIds isSelected={(id) => selected.has(id)} onAdd={addConcept} />
          </div>

          <SelectedConcepts
            concepts={[...selected.values()]}
            onRemove={removeConcept}
            onConfirm={confirm}
            confirming={mapConcepts.isPending}
          />
        </div>
      </CardContent>
    </Card>
  );
}
