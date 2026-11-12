import type { ColumnDef } from "@tanstack/react-table";
import { Check, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useDecideSuggestions, useSuggestions, type SuggestionFilters } from "@/api/queries";
import type { Suggestion } from "@/api/types";
import { EmptyState, ErrorAlert, PageHeader } from "@/components/common";
import { formatDate } from "@/lib/format";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable } from "@/components/DataTable";
import { VocabularySelector } from "@/components/VocabularySelector";
import { useSelectedVocabulary } from "@/hooks/useSelectedVocabulary";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Status = SuggestionFilters["status"];

const SORTS = [
  { value: "confidence_desc", label: "Highest confidence" },
  { value: "confidence_asc", label: "Lowest confidence" },
  { value: "freq_desc", label: "Most frequent" },
  { value: "newest", label: "Newest" },
];
const LEVELS = Array.from({ length: 10 }, (_, i) => i + 1);

function ConfidenceBadge({ value }: { value: number }) {
  const variant = value >= 8 ? "default" : value >= 5 ? "secondary" : "outline";
  return <Badge variant={variant}>{value}</Badge>;
}

export function ReviewPage() {
  const selection = useSelectedVocabulary();
  const vocabularyId = selection.vocabularyId;
  const [status, setStatus] = useState<Status>("pending");
  const [minConfidence, setMinConfidence] = useState<number | undefined>(undefined);
  const [sort, setSort] = useState("confidence_desc");
  const [perPage, setPerPage] = useState(50);
  // Paging resets when the view changes, and the selection when the page changes: both are stored
  // with the view they belong to instead of being reset by effects
  const view = `${vocabularyId}|${status}|${minConfidence}|${sort}|${perPage}`;
  const [paging, setPaging] = useState({ view, page: 1 });
  const page = paging.view === view ? paging.page : 1;
  const setPage = (next: number) => setPaging({ view, page: next });
  const selectionKey = `${view}|${page}`;
  const [selection_, setSelection] = useState<{ key: string; ids: string[] }>({ key: selectionKey, ids: [] });
  const selected = selection_.key === selectionKey ? selection_.ids : [];
  const setSelected = (ids: string[]) => setSelection({ key: selectionKey, ids });
  const [bulkLevel, setBulkLevel] = useState(8);

  const filters: SuggestionFilters = { status, min_confidence: minConfidence, page, per_page: perPage, sort };
  const suggestions = useSuggestions(vocabularyId, filters);
  const accept = useDecideSuggestions(vocabularyId, "accept");
  const reject = useDecideSuggestions(vocabularyId, "reject");
  const busy = accept.isPending || reject.isPending;

  const data = suggestions.data;
  const pending = status === "pending";

  const decide = (
    kind: "accept" | "reject",
    body: { ids?: number[]; vocabulary_id?: number; min_confidence?: number },
  ) => {
    const mutation = kind === "accept" ? accept : reject;
    mutation.mutate(body, {
      onSuccess: (result) => {
        toast.success(`${kind === "accept" ? "Accepted" : "Rejected"} ${result.count} suggestion(s)`);
        setSelected([]);
      },
      onError: (e) => toast.error(errorMessage(e)),
    });
  };

  const columns = useMemo<ColumnDef<Suggestion, unknown>[]>(() => {
    const cols: ColumnDef<Suggestion, unknown>[] = [
      {
        accessorKey: "source_value",
        header: "Source code",
        cell: ({ getValue }) => <span className="font-mono text-xs">{String(getValue())}</span>,
      },
      { accessorKey: "source_concept_name", header: "Source name" },
      {
        accessorKey: "freq",
        header: "Freq",
        cell: ({ getValue }) => (getValue() as number | null)?.toLocaleString() ?? "—",
      },
      {
        id: "concept",
        accessorKey: "concept_name",
        header: "→ Suggested concept",
        cell: ({ row }) => (
          <div>
            <div className="font-medium">{row.original.concept_name}</div>
            <div className="text-muted-foreground text-xs">
              {row.original.concept_id} · {row.original.vocabulary_id} · {row.original.concept_class_id}
            </div>
          </div>
        ),
      },
      { accessorKey: "domain_id", header: "Domain" },
      {
        accessorKey: "confidence",
        header: "Confidence",
        cell: ({ getValue }) => <ConfidenceBadge value={getValue() as number} />,
      },
      {
        accessorKey: "method",
        header: "Method",
        cell: ({ getValue }) => <span className="text-xs">{String(getValue())}</span>,
      },
      {
        accessorKey: "pipeline",
        header: "Pipeline",
        cell: ({ getValue }) => <span className="text-xs">{(getValue() as string | null) ?? "—"}</span>,
      },
      {
        accessorKey: "rationale",
        header: "Rationale",
        enableSorting: false,
        cell: ({ getValue }) => {
          const text = (getValue() as string | null) ?? "";
          return (
            <span className="line-clamp-2 max-w-64 text-xs" title={text}>
              {text || "—"}
            </span>
          );
        },
      },
    ];
    if (!pending) {
      cols.push(
        {
          accessorKey: "decided_by",
          header: "Decided by",
          cell: ({ getValue }) => (getValue() as string | null) ?? "—",
        },
        {
          accessorKey: "decided_at",
          header: "Decided at",
          cell: ({ getValue }) => formatDate(getValue() as string | null),
        },
      );
    }
    return cols;
  }, [pending]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Review auto-mapping suggestions"
        description="Auto-mapping in review mode queues its confident matches here. Accepting a suggestion maps the source concept and records it in the audit trail."
      />
      <VocabularySelector selection={selection} showDrug={false} />

      {vocabularyId !== null && (
        <>
          <div className="flex flex-wrap items-end gap-4">
            <Tabs value={status} onValueChange={(v) => setStatus(v as Status)}>
              <TabsList>
                <TabsTrigger value="pending">Pending</TabsTrigger>
                <TabsTrigger value="accepted">Accepted</TabsTrigger>
                <TabsTrigger value="rejected">Rejected</TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="grid gap-1.5">
              <Label>Min. confidence</Label>
              <Select
                value={minConfidence === undefined ? "any" : String(minConfidence)}
                onValueChange={(v) => setMinConfidence(v === "any" ? undefined : Number(v))}
              >
                <SelectTrigger className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="any">Any</SelectItem>
                  {LEVELS.map((n) => (
                    <SelectItem key={n} value={String(n)}>
                      ≥ {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>Sort</Label>
              <Select value={sort} onValueChange={setSort}>
                <SelectTrigger className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SORTS.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>Per page</Label>
              <Select value={String(perPage)} onValueChange={(v) => setPerPage(Number(v))}>
                <SelectTrigger className="w-24">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[25, 50, 100].map((n) => (
                    <SelectItem key={n} value={String(n)}>
                      {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {pending && (data?.total ?? 0) > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                disabled={selected.length === 0 || busy}
                onClick={() => decide("accept", { ids: selected.map(Number) })}
              >
                <Check /> Accept selected ({selected.length})
              </Button>
              <Button
                variant="outline"
                disabled={selected.length === 0 || busy}
                onClick={() => decide("reject", { ids: selected.map(Number) })}
              >
                <X /> Reject selected
              </Button>
              <div className="bg-border mx-2 hidden h-6 w-px sm:block" />
              <Select value={String(bulkLevel)} onValueChange={(v) => setBulkLevel(Number(v))}>
                <SelectTrigger className="w-24" aria-label="Bulk confidence level">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LEVELS.map((n) => (
                    <SelectItem key={n} value={String(n)}>
                      ≥ {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <ConfirmDialog
                trigger={
                  <Button variant="secondary" disabled={busy}>
                    Accept all ≥ {bulkLevel}
                  </Button>
                }
                title={`Accept all suggestions with confidence ≥ ${bulkLevel}?`}
                description={`Every pending suggestion in ${selection.vocabulary?.name} with confidence ${bulkLevel} or higher becomes a mapping.`}
                confirmLabel="Accept all"
                onConfirm={() => decide("accept", { vocabulary_id: vocabularyId, min_confidence: bulkLevel })}
              />
              <ConfirmDialog
                trigger={
                  <Button variant="outline" disabled={busy}>
                    Reject all ≥ {bulkLevel}
                  </Button>
                }
                title={`Reject all suggestions with confidence ≥ ${bulkLevel}?`}
                description={`Every pending suggestion in ${selection.vocabulary?.name} with confidence ${bulkLevel} or higher is rejected.`}
                confirmLabel="Reject all"
                destructive
                onConfirm={() => decide("reject", { vocabulary_id: vocabularyId, min_confidence: bulkLevel })}
              />
            </div>
          )}

          <ErrorAlert error={suggestions.error} title="Could not load suggestions" />

          {data && data.total === 0 && !suggestions.isFetching ? (
            <EmptyState title={pending ? "No pending suggestions" : `No ${status} suggestions`}>
              {pending && (
                <>
                  <Link to="/map" className="underline">
                    Run auto-mapping in review mode
                  </Link>{" "}
                  to queue suggestions for this vocabulary.
                </>
              )}
            </EmptyState>
          ) : (
            <>
              <DataTable
                columns={columns}
                data={data?.items ?? []}
                getRowId={(row) => String(row.id)}
                selection={pending ? "multi" : "none"}
                selected={selected}
                onSelectedChange={setSelected}
                isLoading={suggestions.isLoading}
                maxHeight="40rem"
              />
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{data?.total.toLocaleString() ?? 0} suggestions</span>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                    <ChevronLeft /> Prev
                  </Button>
                  <span>
                    Page <b>{data?.page ?? page}</b> of <b>{data?.pages ?? 1}</b>
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= (data?.pages ?? 1)}
                    onClick={() => setPage(page + 1)}
                  >
                    Next <ChevronRight />
                  </Button>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
