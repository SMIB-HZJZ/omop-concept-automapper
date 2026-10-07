import { Info, SlidersHorizontal } from "lucide-react";
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
import { useDebounce } from "@/hooks/useDebounce";

const ATC7_RE = /^[A-Z]\d{2}[A-Z]{2}\d{2}$/;

function ResultItem({ result }: { result: SearchResult }) {
  const atc7 = result.atc7_codes ?? [];
  return (
    <AccordionItem value={String(result.concept_id)}>
      <AccordionTrigger>
        <span>
          Rank {result.rank}: {result.concept_name}{" "}
          <span className="text-muted-foreground font-normal">(Score: {result.score.toFixed(3)})</span>
        </span>
      </AccordionTrigger>
      <AccordionContent>
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

  const atc7Code = atc7.trim().toUpperCase();
  const atc7Invalid = atc7Code.length > 0 && !ATC7_RE.test(atc7Code);
  const debouncedQuery = useDebounce(query.trim(), 400);

  const request = useMemo<SearchRequest | null>(() => {
    if (!debouncedQuery) return null;
    return {
      query: debouncedQuery,
      k: Math.min(100, Math.max(1, k || 50)),
      domains,
      vocabulary_id: vocabulary.trim() || null,
      atc7_codes: atc7Code && !atc7Invalid ? [atc7Code] : [],
    };
  }, [debouncedQuery, k, domains, vocabulary, atc7Code, atc7Invalid]);

  const search = useSearch(request);
  const results = search.data?.results ?? [];
  const advancedCount = (vocabulary.trim() ? 1 : 0) + (atc7Code ? 1 : 0);

  return (
    <div className="space-y-6">
      <PageHeader title="Search OMOP Concepts" description="Semantic search over the embedded standard concepts." />

      <div className="grid items-end gap-3 md:grid-cols-[minmax(0,4fr)_minmax(0,2fr)_8rem_auto]">
        <div className="grid gap-1.5">
          <Label htmlFor="search-query">Concept name</Label>
          <Input
            id="search-query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Enter concept name to search..."
            autoFocus
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="search-domains">Domains</Label>
          <MultiSelect id="search-domains" options={meta.data?.domains ?? []} value={domains} onChange={setDomains} placeholder="All domains" />
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
            search.error instanceof ApiError && search.error.status === 409
              ? "No embeddings yet"
              : "Search failed"
          }
        />
      ) : search.isLoading ? (
        <div className="text-muted-foreground text-sm">Searching for similar concepts…</div>
      ) : results.length === 0 ? (
        <Alert>
          <Info />
          <AlertTitle>No similar concepts found. Try adjusting your search terms or filters.</AlertTitle>
        </Alert>
      ) : (
        <div className="space-y-2">
          <div className="text-sm">
            <span className="text-success font-medium">
              Found {results.length} similar concepts for '{request.query}'
            </span>
            <span className="text-muted-foreground"> · {search.data?.method}</span>
            {search.isFetching && <span className="text-muted-foreground"> · updating…</span>}
          </div>
          <Accordion type="multiple" className="rounded-md border px-4">
            {results.map((result) => (
              <ResultItem key={result.concept_id} result={result} />
            ))}
          </Accordion>
        </div>
      )}
    </div>
  );
}
