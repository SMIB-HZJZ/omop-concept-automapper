import type { ColumnDef } from "@tanstack/react-table";
import { Check, ChevronLeft, ChevronRight, ListChecks, Maximize2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
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
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CalibrationPanel } from "./review/CalibrationPanel";
import { ConfidenceMeter } from "./review/ConfidenceMeter";
import { SuggestionDetail, type DetailPosition } from "./review/SuggestionDetail";
import { ConceptLink } from "@/components/ConceptLink";
import { Rationale } from "@/components/Rationale";
import { methodLabel } from "@/lib/methods";

type Status = SuggestionFilters["status"];

const SORTS = [
  { value: "priority", label: "Priority (frequent × uncertain)" },
  { value: "confidence_desc", label: "Highest confidence" },
  { value: "confidence_asc", label: "Lowest confidence" },
  { value: "freq_desc", label: "Most frequent" },
  { value: "newest", label: "Newest" },
];
const LEVELS = Array.from({ length: 10 }, (_, i) => i + 1);

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
  // A released vocabulary is locked: suggestions can be rejected but not accepted
  const locked = selection.vocabulary?.locked ?? false;

  const data = suggestions.data;
  const pending = status === "pending";

  const decide = (
    kind: "accept" | "reject",
    body: { ids?: number[]; vocabulary_id?: number; min_confidence?: number },
  ) => {
    if (kind === "accept" && locked) {
      toast.error("This vocabulary is released and locked. Start the next release on Check & commit first.");
      return;
    }
    const mutation = kind === "accept" ? accept : reject;
    mutation.mutate(body, {
      onSuccess: (result) => {
        toast.success(`${kind === "accept" ? "Accepted" : "Rejected"} ${result.count} suggestion(s)`);
        setSelected([]);
      },
      onError: (e) => toast.error(errorMessage(e)),
    });
  };

  // Keyboard review: J/K or ↓/↑ move the cursor row, A accepts it, R rejects it, X ticks it
  const items = useMemo(() => data?.items ?? [], [data]);
  const [cursor, setCursor] = useState(0);
  // The suggestion open in the details window (by id, with its row as a fallback)
  const [detail, setDetail] = useState<DetailPosition | null>(null);
  const openDetail = (id: number) => {
    const index = items.findIndex((s) => s.id === id);
    if (index >= 0) setDetail({ id, index });
  };
  const openDetailRef = useRef(openDetail);
  useEffect(() => {
    openDetailRef.current = openDetail;
  });
  const cursorRow = items[Math.min(cursor, Math.max(0, items.length - 1))];
  const keys = useRef({ items, cursor, cursorRow, selected, busy, pending });
  useEffect(() => {
    keys.current = { items, cursor, cursorRow, selected, busy, pending };
  });
  const decideRef = useRef(decide);
  useEffect(() => {
    decideRef.current = decide;
  });
  const setSelectedRef = useRef(setSelected);
  useEffect(() => {
    setSelectedRef.current = setSelected;
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.ctrlKey || e.metaKey || e.altKey || document.querySelector('[role="dialog"]')) return;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
      const k = keys.current;
      if (k.items.length === 0) return;
      if (!k.pending && e.key !== "Enter" && !["j", "k", "ArrowDown", "ArrowUp"].includes(e.key)) return;
      const move = (to: number) => {
        const next = Math.min(k.items.length - 1, Math.max(0, to));
        setCursor(next);
        requestAnimationFrame(() =>
          document.querySelector(`[data-row-id="${k.items[next].id}"]`)?.scrollIntoView({ block: "nearest" }),
        );
      };
      const key = e.key.toLowerCase();
      if (key === "j" || e.key === "ArrowDown") move(k.cursor + 1);
      else if (key === "k" || e.key === "ArrowUp") move(k.cursor - 1);
      else if ((key === "a" || key === "r") && k.cursorRow && !k.busy)
        decideRef.current(key === "a" ? "accept" : "reject", { ids: [k.cursorRow.id] });
      else if (e.key === "Enter" && k.cursorRow) openDetailRef.current(k.cursorRow.id);
      else if (key === "x" && k.cursorRow) {
        const id = String(k.cursorRow.id);
        setSelectedRef.current(k.selected.includes(id) ? k.selected.filter((x) => x !== id) : [...k.selected, id]);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const columns = useMemo<ColumnDef<Suggestion, unknown>[]>(() => {
    const cols: ColumnDef<Suggestion, unknown>[] = [
      {
        id: "source",
        header: "Source term",
        cell: ({ row }) => (
          <div className="min-w-40">
            <button
              type="button"
              className="hover:text-primary line-clamp-3 cursor-pointer text-left leading-snug font-medium break-words underline-offset-2 hover:underline"
              title="Show the whole suggestion (Enter)"
              onClick={(e) => {
                e.stopPropagation();
                openDetailRef.current(row.original.id);
              }}
            >
              {row.original.source_concept_name}
            </button>
            <div className="text-muted-foreground text-xs">
              <span className="font-mono">{row.original.source_value}</span>
              {row.original.freq != null && <> · freq {row.original.freq.toLocaleString()}</>}
            </div>
          </div>
        ),
      },
      {
        id: "concept",
        accessorKey: "concept_name",
        header: "→ Suggested concept",
        cell: ({ row }) => (
          <div className="min-w-48">
            <ConceptLink conceptId={row.original.concept_id} className="leading-snug font-medium">
              {row.original.concept_name}
            </ConceptLink>
            <div className="text-muted-foreground text-xs">
              {row.original.domain_id} · {row.original.vocabulary_id} · {row.original.concept_class_id}
            </div>
            {(row.original.extra_concepts ?? []).map((c) => (
              <div key={c.concept_id} className="mt-1">
                <div className="font-medium">
                  + <ConceptLink conceptId={c.concept_id}>{c.concept_name}</ConceptLink>
                </div>
                <div className="text-muted-foreground text-xs">{c.concept_class_id} (mapped together)</div>
              </div>
            ))}
          </div>
        ),
      },
      {
        accessorKey: "confidence",
        header: "Confidence",
        size: 110,
        cell: ({ row }) => (
          <div className="space-y-1">
            <ConfidenceMeter value={row.original.confidence} />
            {row.original.match_type && row.original.match_type !== "exact" && (
              <div className="text-xs text-amber-700 dark:text-amber-400">{row.original.match_type}</div>
            )}
          </div>
        ),
      },
      {
        accessorKey: "rationale",
        header: "Why",
        enableSorting: false,
        cell: ({ row }) => {
          const text = row.original.rationale ?? "";
          return (
            <div className="max-w-80">
              {text ? (
                <button
                  type="button"
                  className="group hover:bg-muted/60 -mx-1 block cursor-pointer rounded-md px-1 text-left"
                  title="Show the whole reason"
                  onClick={(e) => {
                    e.stopPropagation();
                    openDetailRef.current(row.original.id);
                  }}
                >
                  <Rationale text={text} clamp className="text-xs" />
                  <span className="text-primary inline-flex items-center gap-0.5 text-[11px] opacity-70 group-hover:opacity-100">
                    <Maximize2 className="size-3" /> more
                  </span>
                </button>
              ) : (
                "—"
              )}
              <span className="text-muted-foreground text-[11px]">
                {methodLabel(row.original.method)}
                {row.original.pipeline ? ` · ${row.original.pipeline}` : ""}
              </span>
            </div>
          );
        },
      },
    ];
    if (pending) {
      cols.push({
        id: "actions",
        header: "",
        size: 84,
        enableSorting: false,
        cell: ({ row }) => (
          <div className="flex justify-end gap-1">
            <Button
              size="icon-sm"
              variant="outline"
              className="hover:bg-success/15 hover:text-success"
              aria-label="Accept"
              title={locked ? "Locked by a release" : "Accept (A)"}
              disabled={locked}
              onClick={(e) => {
                e.stopPropagation();
                decideRef.current("accept", { ids: [row.original.id] });
              }}
            >
              <Check />
            </Button>
            <Button
              size="icon-sm"
              variant="outline"
              className="hover:bg-destructive/10 hover:text-destructive"
              aria-label="Reject"
              title="Reject (R)"
              onClick={(e) => {
                e.stopPropagation();
                decideRef.current("reject", { ids: [row.original.id] });
              }}
            >
              <X />
            </Button>
          </div>
        ),
      });
    } else {
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
  }, [pending, locked]);

  return (
    <div className="space-y-6">
      <PageHeader
        icon={ListChecks}
        step={3}
        title="Review AI suggestions"
        description="Check what the AI proposed. Accepted suggestions become mappings; rejected ones go back to the terms still to map."
      />
      <VocabularySelector selection={selection} />
      <CalibrationPanel vocabularyId={vocabularyId} />

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
              <Label htmlFor="review-min-confidence">Min. confidence</Label>
              <Select
                value={minConfidence === undefined ? "any" : String(minConfidence)}
                onValueChange={(v) => setMinConfidence(v === "any" ? undefined : Number(v))}
              >
                <SelectTrigger id="review-min-confidence" className="w-28">
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
              <Label htmlFor="review-sort">Sort</Label>
              <Select value={sort} onValueChange={setSort}>
                <SelectTrigger id="review-sort" className="w-44">
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
              <Label htmlFor="review-per-page">Per page</Label>
              <Select value={String(perPage)} onValueChange={(v) => setPerPage(Number(v))}>
                <SelectTrigger id="review-per-page" className="w-24">
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
                variant="cta"
                disabled={selected.length === 0 || busy || locked}
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
                  <Button variant="secondary" disabled={busy || locked}>
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
              {pending && (
                <p className="text-muted-foreground text-xs">
                  Keyboard: <b>J</b>/<b>K</b> or ↓/↑ move · <b>Enter</b> details · <b>A</b> accept · <b>R</b> reject ·{" "}
                  <b>X</b> tick for bulk actions. Click a term or its reason to see it in full.
                </p>
              )}
              <DataTable
                columns={columns}
                data={items}
                getRowId={(row) => String(row.id)}
                highlightId={cursorRow ? String(cursorRow.id) : null}
                selection={pending ? "multi" : "none"}
                selected={selected}
                onSelectedChange={setSelected}
                isLoading={suggestions.isLoading}
                maxHeight="40rem"
              />
              <SuggestionDetail
                items={items}
                position={detail}
                onPositionChange={setDetail}
                onClose={(index) => {
                  setDetail(null);
                  setCursor(index);
                }}
                onDecide={(kind, id) => decide(kind, { ids: [id] })}
                busy={busy}
                pending={pending}
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
