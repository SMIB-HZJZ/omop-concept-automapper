import { Info, Loader2, PanelRightOpen, Search as SearchIcon, SlidersHorizontal } from "lucide-react";
import { motion } from "motion/react";
import { useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import { useMeta, useSearch } from "@/api/queries";
import type { SearchRequest, SearchResult } from "@/api/types";
import { ErrorAlert, PageHeader } from "@/components/common";
import { MultiSelect } from "@/components/MultiSelect";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useConceptModal } from "@/hooks/useConceptModal";
import { useDebounce } from "@/hooks/useDebounce";
import { Skeleton } from "@/components/motion";
import { isValidAtc7, normalizeAtc7 } from "@/lib/atc7";

function ResultItem({ result }: { result: SearchResult }) {
  const atc7 = result.atc7_codes ?? [];
  const { openConcept } = useConceptModal();
  return (
    <AccordionItem value={String(result.concept_id)}>
      <AccordionTrigger>
        <span className="flex min-w-0 flex-1 items-center gap-3">
          <span className="bg-muted text-muted-foreground flex size-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold tabular-nums">
            {result.rank}
          </span>
          <span className="min-w-0">
            <span className="block truncate">{result.concept_name}</span>
            <span className="text-muted-foreground block text-xs font-normal">
              {result.domain_id} · {result.vocabulary_id} · {result.concept_class_id}
            </span>
          </span>
          <span className="text-muted-foreground ml-auto shrink-0 pl-2 text-xs font-normal tabular-nums">
            {result.score.toFixed(3)}
          </span>
        </span>
      </AccordionTrigger>
      <AccordionContent>
        <Button variant="outline" size="sm" className="mb-3 gap-1.5" onClick={() => openConcept(result.concept_id)}>
          <PanelRightOpen /> Details, hierarchy & AI explanation
        </Button>
        <div className="grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <div className="mb-1 font-medium">Concept details</div>
            <ul className="space-y-0.5">
              <li>
                <span className="text-muted-foreground">ID:</span> {result.concept_id}
              </li>
              <li>
                <span className="text-muted-foreground">Name:</span> {result.concept_name}
              </li>
              <li>
                <span className="text-muted-foreground">Domain:</span> {result.domain_id}
              </li>
            </ul>
          </div>
          <div>
            <div className="mb-1 font-medium">Classification</div>
            <ul className="space-y-0.5">
              <li>
                <span className="text-muted-foreground">Class:</span> {result.concept_class_id}
              </li>
              <li>
                <span className="text-muted-foreground">Vocabulary:</span> {result.vocabulary_id}
              </li>
              <li>
                <span className="text-muted-foreground">Code:</span> {result.concept_code}
              </li>
            </ul>
            {atc7.length > 0 && (
              <div className="mt-2">
                <div className="mb-1 font-medium">ATC7 codes</div>
                <div className="flex flex-wrap gap-1">
                  {atc7.slice(0, 5).map((code) => (
                    <Badge key={code} variant="secondary" className="font-mono">
                      {code}
                    </Badge>
                  ))}
                  {atc7.length > 5 && (
                    <span className="text-muted-foreground text-xs">... and {atc7.length - 5} more</span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </AccordionContent>
    </AccordionItem>
  );
}

export function SearchPage() {
  const meta = useMeta();
  const [query, setQuery] = useState("");
  const [domains, setDomains] = useState<string[]>([]);
  const [k, setK] = useState(50);
  const [vocabulary, setVocabulary] = useState("");
  const [atc7, setAtc7] = useState("");

  const atc7Code = normalizeAtc7(atc7);
  const atc7Invalid = atc7Code.length > 0 && !isValidAtc7(atc7Code);
  // Every typed field is debounced, so typing does not send a request per keystroke
  const debouncedQuery = useDebounce(query.trim(), 400);
  const debouncedK = useDebounce(k, 400);
  const debouncedVocabulary = useDebounce(vocabulary.trim(), 400);
  const debouncedAtc7 = useDebounce(atc7Code && !atc7Invalid ? atc7Code : "", 400);

  const request = useMemo<SearchRequest | null>(() => {
    if (!debouncedQuery) return null;
    return {
      query: debouncedQuery,
      k: Math.min(100, Math.max(1, debouncedK || 50)),
      domains,
      vocabulary_id: debouncedVocabulary || null,
      atc7_codes: debouncedAtc7 ? [debouncedAtc7] : [],
    };
  }, [debouncedQuery, debouncedK, domains, debouncedVocabulary, debouncedAtc7]);

  const search = useSearch(request);
  // Typing ahead of the debounce counts as busy too, so the spinner shows right away
  const typing = query.trim() !== debouncedQuery;
  const busy = typing || search.isFetching;
  const results = search.data?.results ?? [];
  const advancedCount = (vocabulary.trim() ? 1 : 0) + (atc7Code ? 1 : 0);

  return (
    <div className="space-y-6">
      <PageHeader
        icon={SearchIcon}
        title="Search concepts"
        description="Find standard OMOP concepts by meaning, in any language. Only embedded concepts are searched."
      />

      <div className="grid items-end gap-3 md:grid-cols-[minmax(0,4fr)_minmax(0,2fr)_8rem_auto]">
        <div className="grid gap-1.5">
          <Label htmlFor="search-query">Concept name</Label>
          <div className="relative">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
            <Input
              id="search-query"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. šećerna bolest tip 2, metformin 500 mg"
              className="pr-9 pl-9"
              autoFocus
            />
            {query.trim() && busy && (
              <Loader2
                aria-label="Searching"
                className="text-primary absolute top-1/2 right-3 size-4 -translate-y-1/2 animate-spin"
              />
            )}
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="search-domains">Domains</Label>
          <MultiSelect
            id="search-domains"
            options={meta.data?.domains ?? []}
            value={domains}
            onChange={setDomains}
            placeholder="All domains"
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="search-k">Number of results</Label>
          <Input
            id="search-k"
            type="number"
            min={1}
            max={100}
            value={k}
            onChange={(e) => setK(Number(e.target.value))}
          />
        </div>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className="gap-2">
              <SlidersHorizontal className="size-4" />
              Advanced filters
              {advancedCount > 0 && <Badge className="h-5 px-1.5">{advancedCount}</Badge>}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 space-y-3">
            <div className="grid gap-1.5">
              <Label htmlFor="search-vocabulary">Vocabulary</Label>
              <Input
                id="search-vocabulary"
                value={vocabulary}
                onChange={(e) => setVocabulary(e.target.value)}
                placeholder="e.g. RxNorm, SNOMED"
              />
              <p className="text-muted-foreground text-xs">Filter by a specific vocabulary</p>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="search-atc7">ATC7 code</Label>
              <Input
                id="search-atc7"
                value={atc7}
                maxLength={7}
                onChange={(e) => setAtc7(e.target.value.toUpperCase())}
                placeholder="e.g. A02BC01"
                className="font-mono"
                aria-invalid={atc7Invalid}
              />
              {atc7Invalid ? (
                <p className="text-destructive text-xs">
                  Invalid ATC7 code. Expected letter-digit-digit-letter-letter-digit-digit (e.g. A10BA02).
                </p>
              ) : (
                <p className="text-muted-foreground text-xs">Filter drugs by ATC7 code</p>
              )}
            </div>
          </PopoverContent>
        </Popover>
      </div>

      {!request ? (
        <Alert>
          <Info />
          <AlertTitle>Enter a concept name above to search for similar OMOP concepts.</AlertTitle>
          <AlertDescription>
            <div className="mt-2 font-medium">Search tips</div>
            <ul className="list-disc pl-5">
              <li>Filter by domain to narrow down results</li>
              <li>Use ATC7 codes to find drugs in specific therapeutic classes</li>
              <li>Try filtering by vocabularies (e.g. 'RxNorm' for drugs)</li>
            </ul>
          </AlertDescription>
        </Alert>
      ) : search.isError ? (
        <ErrorAlert
          error={search.error}
          title={
            search.error instanceof ApiError && search.error.status === 409 ? "No embeddings yet" : "Search failed"
          }
        />
      ) : search.isLoading || (typing && !search.data) ? (
        <div className="space-y-2" role="status" aria-label="Searching">
          <Skeleton className="h-4 w-72" />
          <div className="space-y-3 rounded-md border p-4">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="flex items-center gap-3" style={{ opacity: 1 - i * 0.13 }}>
                <Skeleton className="size-7" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/3" />
                </div>
                <Skeleton className="h-3 w-10" />
              </div>
            ))}
          </div>
        </div>
      ) : results.length === 0 ? (
        <Alert>
          <Info />
          <AlertTitle>No similar concepts found. Try adjusting your search terms or filters.</AlertTitle>
        </Alert>
      ) : (
        <div className="space-y-2">
          <div className="text-sm">
            <span className="text-success font-medium">
              Found {results.length} similar concepts for '{search.data?.request.query ?? request.query}'
            </span>
            <span className="text-muted-foreground"> · {search.data?.method}</span>
          </div>
          <div className="relative">
            {/* Refreshing: a thin moving bar and slightly dimmed results */}
            {busy && (
              <div className="bg-primary/15 absolute inset-x-0 top-0 z-10 h-0.5 overflow-hidden rounded-full">
                <motion.div
                  className="bg-brand h-full w-1/3 rounded-full"
                  initial={{ x: "-100%" }}
                  animate={{ x: "300%" }}
                  transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
                />
              </div>
            )}
            <motion.div
              key={search.data?.request.query}
              animate={{ opacity: busy ? 0.55 : 1 }}
              transition={{ duration: 0.2 }}
            >
              <Accordion type="multiple" className="bg-card rounded-xl border px-4 shadow-[var(--shadow-soft)]">
                {results.map((result, i) => (
                  <motion.div
                    key={result.concept_id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.25, delay: Math.min(i, 12) * 0.03 }}
                  >
                    <ResultItem result={result} />
                  </motion.div>
                ))}
              </Accordion>
            </motion.div>
          </div>
        </div>
      )}
    </div>
  );
}
