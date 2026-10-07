import type { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useAuditRecent, useAuditStats, useMappingStats, useUnmapped } from "@/api/queries";
import type { AuditEntry, UnmappedItem } from "@/api/types";
import { DataTable } from "@/components/DataTable";
import { EmptyState, ErrorAlert, PageHeader, StatCard, formatDate, formatNumber } from "@/components/common";
import { VocabularySelector, useSelectedVocabulary } from "@/components/VocabularySelector";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAutomapControls } from "./map/AutomapSettings";
import { ConceptDetail } from "./map/ConceptDetail";

const UNMAPPED_COLUMNS: ColumnDef<UnmappedItem, unknown>[] = [
  { accessorKey: "source_value", header: "Source code", cell: ({ row }) => <span className="font-mono">{row.original.source_value}</span> },
  { accessorKey: "source_concept_name", header: "Source concept name" },
  { accessorKey: "mapped_concepts", header: "# Mapped" },
  { accessorKey: "freq", header: "Frequency" },
  {
    id: "suggestion",
    header: "Suggestion",
    enableSorting: false,
    cell: ({ row }) =>
      row.original.suggestion_concept_id ? (
        <Badge variant="secondary" title={`Concept ${row.original.suggestion_concept_id}`}>
          pending · {row.original.suggestion_confidence}
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

export function MapPage() {
  const selection = useSelectedVocabulary();
  const vocabularyId = selection.vocabularyId;
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);

  const stats = useMappingStats(vocabularyId);
  const unmapped = useUnmapped(vocabularyId, page, perPage);
  const automap = useAutomapControls({ vocabularyId: vocabularyId ?? 0, isDrug: selection.isDrug });

  useEffect(() => {
    setPage(1);
    setSelectedSource(null);
  }, [vocabularyId]);

  const pages = unmapped.data?.pages ?? 1;
  useEffect(() => {
    if (page > pages) setPage(pages);
  }, [page, pages]);

  const items = unmapped.data?.items ?? [];
  const concept = items.find((i) => String(i.source_id) === selectedSource) ?? null;

  const afterMapped = () => {
    const idx = items.findIndex((i) => String(i.source_id) === selectedSource);
    const next = items[idx + 1] ?? items[idx - 1];
    setSelectedSource(next ? String(next.source_id) : null);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Map Concepts"
        description={
          <>
            <b>Auto map all</b>: map every unmapped concept with AI similarity search. <b>Manual mapping</b>: review and map
            single concepts with search and filters. Tip: import the vocabulary and embed concepts (Import Data) before mapping.
          </>
        }
      />
      <VocabularySelector selection={selection} />
      {vocabularyId === null ? null : (
        <>
          <div className="space-y-3">
            <h2 className="text-lg font-semibold">Mapping progress</h2>
            <ErrorAlert error={stats.error} />
            {stats.data && (
              <>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <StatCard label="Total concepts" value={formatNumber(stats.data.total)} />
                  <StatCard label="Mapped" value={formatNumber(stats.data.mapped)} />
                  <StatCard label="Unmapped" value={formatNumber(stats.data.unmapped)} />
                  <StatCard label="Progress" value={`${stats.data.percentage.toFixed(1)}%`} />
                </div>
                <Progress value={stats.data.percentage} />
                {stats.data.pending_suggestions > 0 && (
                  <p className="text-sm">
                    {formatNumber(stats.data.pending_suggestions)} auto-map suggestions wait for review —{" "}
                    <Link to="/review" className="text-primary underline">
                      open the review queue
                    </Link>
                  </p>
                )}
              </>
            )}
            <AuditTrail vocabularyId={vocabularyId} />
          </div>

          {stats.data && stats.data.unmapped === 0 ? (
            <EmptyState title="All concepts in this vocabulary have been mapped!" icon={<CheckCircle2 className="text-success size-8" />}>
              Switch to a different vocabulary or import more concepts on the Import Data page.
            </EmptyState>
          ) : (
            <>
              <div className="flex flex-wrap items-end gap-4">
                <div className="grid gap-1.5">
                  <Label htmlFor="page">Page</Label>
                  <Input
                    id="page"
                    type="number"
                    className="w-24"
                    min={1}
                    max={pages}
                    value={page}
                    onChange={(e) => setPage(Math.min(Math.max(1, Number(e.target.value) || 1), pages))}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label>Concepts per page</Label>
                  <Select value={String(perPage)} onValueChange={(v) => { setPerPage(Number(v)); setPage(1); }}>
                    <SelectTrigger className="w-28">
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
                {automap.trigger}
                <div className="text-sm">
                  Page <b>{unmapped.data?.page ?? page}</b> of <b>{pages}</b>
                </div>
              </div>
              {automap.progress}

              <Card>
                <CardContent className="space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="font-medium">
                      Unmapped concepts (showing {items.length} of {formatNumber(unmapped.data?.total)})
                    </span>
                    <span className="text-muted-foreground text-xs">Click a row to start mapping</span>
                  </div>
                  <ErrorAlert error={unmapped.error} />
                  <DataTable
                    columns={UNMAPPED_COLUMNS}
                    data={items}
                    getRowId={(r) => String(r.source_id)}
                    selection="single"
                    selected={selectedSource ? [selectedSource] : []}
                    onSelectedChange={(ids) => setSelectedSource(ids[0] ?? null)}
                    isLoading={unmapped.isLoading}
                    emptyText="No unmapped concepts on this page."
                    maxHeight="24rem"
                  />
                </CardContent>
              </Card>

              {concept ? (
                <ConceptDetail
                  key={concept.source_id}
                  concept={concept}
                  vocabularyId={vocabularyId}
                  isDrug={selection.isDrug}
                  onMapped={afterMapped}
                />
              ) : (
                items.length > 0 && (
                  <p className="text-muted-foreground text-sm">Select a concept from the table above to start mapping.</p>
                )
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
