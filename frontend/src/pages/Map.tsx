import { useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, ChevronDown, Map as MapIcon, MousePointerClick, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { api, unwrap } from "@/api/client";
import { keys, useAuditRecent, useAuditStats, useMappingStats, useUnmapped } from "@/api/queries";
import { usePersistentState } from "@/hooks/usePersistentState";
import type { AuditEntry, UnmappedItem } from "@/api/types";
import { DataTable } from "@/components/DataTable";
import { EmptyState, ErrorAlert, PageHeader, StatCard } from "@/components/common";
import { AnimatedNumber } from "@/components/motion";
import { PageControls } from "@/components/Pagination";
import { formatDate, formatNumber } from "@/lib/format";
import { VocabularySelector } from "@/components/VocabularySelector";
import { useSelectedVocabulary } from "@/hooks/useSelectedVocabulary";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Progress } from "@/components/ui/progress";
import { useAutomapControls } from "./map/AutomapSettings";
import { ConceptDetail } from "./map/ConceptDetail";
import { defaultSearchRequest } from "./map/searchRequest";

const UNMAPPED_COLUMNS: ColumnDef<UnmappedItem, unknown>[] = [
  {
    id: "term",
    header: "Source term",
    cell: ({ row }) => (
      <div className="min-w-0">
        <div className="leading-snug font-medium">{row.original.source_concept_name}</div>
        <div className="text-muted-foreground truncate font-mono text-xs">{row.original.source_value}</div>
      </div>
    ),
  },
  {
    accessorKey: "freq",
    header: () => <div className="text-right">Freq</div>,
    size: 64,
    cell: ({ row }) => (
      <div className="text-muted-foreground text-right text-xs tabular-nums">
        {row.original.freq?.toLocaleString() ?? "—"}
      </div>
    ),
  },
  {
    id: "suggestion",
    header: "",
    size: 56,
    cell: ({ row }) =>
      row.original.suggestion_concept_id ? (
        <Badge
          variant="secondary"
          className="gap-1"
          title={`An AI suggestion (confidence ${row.original.suggestion_confidence}) waits in Review`}
        >
          <Sparkles className="size-3" /> {row.original.suggestion_confidence}
        </Badge>
      ) : null,
  },
];

const RECENT_COLUMNS: ColumnDef<AuditEntry, unknown>[] = [
  { accessorKey: "source_concept_name", header: "Source concept" },
  { accessorKey: "mapped_concept_name", header: "Mapped concept" },
  { accessorKey: "confidence_score", header: "Confidence" },
  { accessorKey: "mapping_method", header: "Method" },
  { accessorKey: "created_by", header: "By", cell: ({ row }) => row.original.created_by ?? "—" },
  { accessorKey: "created_at", header: "Created", cell: ({ row }) => formatDate(row.original.created_at) },
];

function AuditTrail({ vocabularyId }: { vocabularyId: number }) {
  const [open, setOpen] = useState(false);
  const stats = useAuditStats(open ? vocabularyId : null);
  const recent = useAuditRecent(open ? vocabularyId : null, 10);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border">
      <CollapsibleTrigger className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium">
        Auto-mapping audit trail
        <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-4 border-t p-4">
        {stats.data && stats.data.length === 0 ? (
          <p className="text-muted-foreground text-sm">No auto-mapping statistics available for this vocabulary.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {stats.data?.map((s) => (
              <StatCard
                key={s.mapping_method}
                label={`${s.mapping_method} mappings`}
                value={formatNumber(s.mapping_count)}
                hint={s.avg_confidence != null ? `Avg confidence ${s.avg_confidence.toFixed(2)}` : undefined}
              />
            ))}
          </div>
        )}
        <div className="text-sm font-medium">Recent mappings</div>
        <DataTable
          columns={RECENT_COLUMNS}
          data={recent.data ?? []}
          getRowId={(r) => String(r.audit_id)}
          isLoading={recent.isLoading}
          emptyText="No mapping history found for this vocabulary."
          maxHeight="16rem"
        />
      </CollapsibleContent>
    </Collapsible>
  );
}

/** Mapped / total with a bar, what is left, and a link to waiting suggestions. */
function ProgressStrip({ vocabularyId, action }: { vocabularyId: number; action: ReactNode }) {
  const stats = useMappingStats(vocabularyId);
  const d = stats.data;
  return (
    <Card className="gap-0 py-3">
      <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4">
        <ErrorAlert error={stats.error} />
        <div className="min-w-56 flex-1 space-y-1.5">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span>
              <b className="tabular-nums">{d ? <AnimatedNumber value={d.mapped} /> : "—"}</b>
              <span className="text-muted-foreground"> of {formatNumber(d?.total)} terms mapped</span>
            </span>
            <span className="text-primary font-semibold tabular-nums">
              {d ? <AnimatedNumber value={d.percentage} decimals={1} suffix="%" /> : ""}
            </span>
          </div>
          <Progress value={d?.percentage ?? 0} aria-label="Mapped terms" />
        </div>
        {d && d.pending_suggestions > 0 && (
          <Link
            to="/review"
            className="bg-accent text-accent-foreground hover:bg-accent/70 flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm transition-colors"
          >
            <Sparkles className="size-4" />
            <b className="tabular-nums">{formatNumber(d.pending_suggestions)}</b> suggestions to review →
          </Link>
        )}
        <div className="ml-auto">{action}</div>
      </CardContent>
    </Card>
  );
}

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

/** Everything below the vocabulary selector; keyed by vocabulary so paging and selection reset. */
function VocabularyMapping({ vocabularyId, isDrug }: { vocabularyId: number; isDrug: boolean }) {
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);

  const stats = useMappingStats(vocabularyId);
  const unmapped = useUnmapped(vocabularyId, page, perPage);
  const automap = useAutomapControls({ vocabularyId, isDrug });

  // The API clamps an out-of-range page (e.g. after mapping the last rows of the last page)
  const pages = unmapped.data?.pages ?? 1;
  const currentPage = unmapped.data?.page ?? page;

  const items = useMemo(() => unmapped.data?.items ?? [], [unmapped.data]);
  const concept = items.find((i) => String(i.source_id) === selectedSource) ?? null;

  const select = useCallback((id: string | null) => {
    setSelectedSource(id);
    if (id)
      requestAnimationFrame(() =>
        document.querySelector(`[data-row-id="${id}"]`)?.scrollIntoView({ block: "nearest" }),
      );
  }, []);

  const afterMapped = () => {
    const idx = items.findIndex((i) => String(i.source_id) === selectedSource);
    const next = items[idx + 1] ?? items[idx - 1];
    select(next ? String(next.source_id) : null);
  };

  // ↑/↓ move through the source terms (not while typing or with a dialog open)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      if (isTyping(e.target) || document.querySelector('[role="dialog"]') || items.length === 0) return;
      e.preventDefault();
      const idx = items.findIndex((i) => String(i.source_id) === selectedSource);
      const nextIdx = idx < 0 ? 0 : Math.min(items.length - 1, Math.max(0, idx + (e.key === "ArrowDown" ? 1 : -1)));
      select(String(items[nextIdx].source_id));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, selectedSource, select]);

  // Load the next term's candidates in the background, so moving on is instant
  const qc = useQueryClient();
  const [mapDomains] = usePersistentState<string[]>("map-domains", []);
  useEffect(() => {
    const idx = items.findIndex((i) => String(i.source_id) === selectedSource);
    const next = idx >= 0 ? items[idx + 1] : undefined;
    if (!next) return;
    const body = defaultSearchRequest(next, isDrug, mapDomains);
    void qc.prefetchQuery({
      queryKey: keys.search(body),
      queryFn: async () => ({ ...unwrap(await api.POST("/api/search", { body })), request: body }),
      staleTime: 60_000,
    });
  }, [items, selectedSource, isDrug, mapDomains, qc]);

  const allMapped = stats.data && stats.data.unmapped === 0;

  return (
    <>
      <ProgressStrip vocabularyId={vocabularyId} action={!allMapped && automap.trigger} />
      {automap.progress}

      {allMapped ? (
        <EmptyState
          title="All concepts in this vocabulary have been mapped!"
          icon={<CheckCircle2 className="text-success size-8" />}
        >
          Switch to a different vocabulary or import more concepts on the Import Data page.
        </EmptyState>
      ) : (
        /* Source terms on the left, their candidate standard concepts on the right; both columns
           have a fixed height on wide screens, so switching terms never moves the page */
        <div className="grid gap-5 xl:h-[max(36rem,calc(100vh-19rem))] xl:grid-cols-[minmax(0,4fr)_minmax(0,8fr)]">
          <Card className="min-h-0 gap-0 py-0">
            <CardContent className="flex h-full min-h-0 flex-col gap-3 p-4">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="font-semibold">To map</h2>
                <span className="text-muted-foreground text-xs">
                  {formatNumber(unmapped.data?.total)} terms · most frequent first · ↑↓ to move
                </span>
              </div>
              <ErrorAlert error={unmapped.error} />
              <DataTable
                columns={UNMAPPED_COLUMNS}
                data={items}
                getRowId={(r) => String(r.source_id)}
                selection="single"
                selected={selectedSource ? [selectedSource] : []}
                // Clicking the selected row again keeps it selected
                onSelectedChange={(ids) => ids[0] && select(ids[0])}
                sortable={false}
                isLoading={unmapped.isLoading}
                emptyText="No unmapped concepts on this page."
                maxHeight="none"
                className="max-h-[32rem] min-h-0 flex-1 xl:max-h-none"
              />
              <PageControls
                idPrefix="unmapped"
                page={currentPage}
                pages={pages}
                perPage={perPage}
                onPageChange={setPage}
                onPerPageChange={setPerPage}
                itemLabel="Terms"
              />
            </CardContent>
          </Card>

          <div className="min-h-0">
            {concept ? (
              <ConceptDetail
                key={concept.source_id}
                concept={concept}
                vocabularyId={vocabularyId}
                isDrug={isDrug}
                onMapped={afterMapped}
                onUndone={(id) => select(String(id))}
              />
            ) : (
              <div className="h-full">
                <EmptyState title="Pick a source term" icon={<MousePointerClick className="text-primary size-7" />}>
                  Click a term on the left, or press ↓. Its matching standard concepts appear here, ready to select and
                  confirm.
                </EmptyState>
              </div>
            )}
          </div>
        </div>
      )}

      <AuditTrail vocabularyId={vocabularyId} />
    </>
  );
}

export function MapPage() {
  const selection = useSelectedVocabulary();
  const vocabularyId = selection.vocabularyId;
  return (
    <div className="space-y-6">
      <PageHeader
        icon={MapIcon}
        step={2}
        title="Map concepts"
        description={
          <>
            Let the AI map all remaining terms with <b>Auto-map with AI</b>, or pick a term in the table to map it by
            hand.
          </>
        }
      />
      <VocabularySelector selection={selection} />
      {vocabularyId !== null && (
        <VocabularyMapping key={vocabularyId} vocabularyId={vocabularyId} isDrug={selection.isDrug} />
      )}
    </div>
  );
}
