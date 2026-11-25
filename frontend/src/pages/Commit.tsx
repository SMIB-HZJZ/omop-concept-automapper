import type { ColumnDef } from "@tanstack/react-table";
import { ClipboardCheck, Download, Trash2, Unlink } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useDeleteVocabulary, useMapped, useMappingStats, useUnmap } from "@/api/queries";
import type { MappedRow } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ErrorAlert, PageHeader, StatCard } from "@/components/common";
import { PageControls } from "@/components/Pagination";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable } from "@/components/DataTable";
import { VocabularySelector } from "@/components/VocabularySelector";
import { useSelectedVocabulary } from "@/hooks/useSelectedVocabulary";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { useDebounce } from "@/hooks/useDebounce";
import { formatNumber } from "@/lib/format";
import { ConceptLink } from "@/components/ConceptLink";
import { MappingFlag } from "./commit/MappingFlag";
import { ReleasePanel } from "./commit/ReleasePanel";
import { Switch } from "@/components/ui/switch";

const chartConfig = { count: { label: "Mappings", color: "var(--chart-1)" } } satisfies ChartConfig;

const rowId = (row: MappedRow) => `${row.source_id}:${row.concept_id}`;

const baseColumns: ColumnDef<MappedRow, unknown>[] = [
  {
    accessorKey: "source_value",
    header: "Source code",
    cell: ({ getValue }) => <span className="font-mono text-xs">{String(getValue())}</span>,
  },
  { accessorKey: "source_concept_name", header: "Source concept name" },
  {
    accessorKey: "concept_name",
    header: "OMOP concept name",
    cell: ({ row }) => <ConceptLink conceptId={row.original.concept_id}>{row.original.concept_name}</ConceptLink>,
  },
  { accessorKey: "concept_id", header: "Concept ID" },
  { accessorKey: "domain_id", header: "Domain" },
  {
    accessorKey: "match_type",
    header: "Match",
    cell: ({ getValue }) => {
      const value = getValue() as string | null;
      if (!value) return <span className="text-muted-foreground">—</span>;
      return value === "exact" ? value : <span className="text-amber-700 dark:text-amber-400">{value}</span>;
    },
  },
  {
    accessorKey: "freq",
    header: "Freq",
    cell: ({ getValue }) => (getValue() as number | null)?.toLocaleString() ?? "—",
  },
  { accessorKey: "mapped_by", header: "Mapped by", cell: ({ getValue }) => (getValue() as string | null) ?? "—" },
];

function MappedTable({
  vocabularyId,
  locked,
  actions,
}: {
  vocabularyId: number;
  locked: boolean;
  actions: (selection: ReactNode) => ReactNode;
}) {
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [query, setQuery] = useState("");
  const q = useDebounce(query.trim(), 400);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const mapped = useMapped(vocabularyId, page, perPage, q, flaggedOnly);
  const columns = useMemo<ColumnDef<MappedRow, unknown>[]>(
    () => [
      ...baseColumns,
      {
        id: "flag",
        header: "",
        size: 48,
        cell: ({ row }) => <MappingFlag row={row.original} vocabularyId={vocabularyId} />,
      },
    ],
    [vocabularyId],
  );
  const unmap = useUnmap(vocabularyId);
  const [selected, setSelected] = useState<string[]>([]);

  const selectedSources = useMemo(() => [...new Set(selected.map((id) => Number(id.split(":")[0])))], [selected]);
  const data = mapped.data;

  const onUnmap = () =>
    unmap.mutate(selectedSources, {
      onSuccess: () => {
        toast.success(`Unmapped ${selectedSources.length} concepts`);
        setSelected([]);
      },
      onError: (e) => toast.error(errorMessage(e)),
    });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-4">
        <div className="grid min-w-64 flex-1 gap-1.5">
          <Label htmlFor="mapped-search">Search mappings</Label>
          <Input
            id="mapped-search"
            placeholder="Source code or name, OMOP concept name or ID"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="flex h-9 items-center gap-2">
          <Switch
            id="mapped-flagged"
            checked={flaggedOnly}
            onCheckedChange={(v) => {
              setFlaggedOnly(v);
              setPage(1);
            }}
          />
          <Label htmlFor="mapped-flagged" className="font-normal">
            Mappings with a question
          </Label>
        </div>
        <PageControls
          idPrefix="mapped"
          page={data?.page ?? page}
          pages={data?.pages ?? 1}
          perPage={perPage}
          onPageChange={setPage}
          onPerPageChange={setPerPage}
          itemLabel="Rows"
        />
      </div>
      <ErrorAlert error={mapped.error} title="Could not load mappings" />
      <div className="text-muted-foreground text-xs">
        Showing {formatNumber(data?.items.length)} of {formatNumber(data?.total)} mappings{q ? ` matching "${q}"` : ""},
        sorted by frequency
        {selected.length > 0 && ` · ${selectedSources.length} source concept(s) selected (kept across pages)`}
      </div>
      <DataTable
        columns={columns}
        data={data?.items ?? []}
        getRowId={rowId}
        selection="multi"
        selected={selected}
        onSelectedChange={(ids) =>
          // Keep selections made on other pages
          setSelected((prev) => [...prev.filter((id) => !(data?.items ?? []).some((r) => rowId(r) === id)), ...ids])
        }
        sortable={false}
        isLoading={mapped.isLoading}
        emptyText={
          flaggedOnly
            ? "No mapped term has an open question. Unmapped terms someone flagged are under Map concepts → Flagged."
            : q
              ? "No mappings match your search."
              : "No mapped concepts found for this vocabulary."
        }
        maxHeight="36rem"
      />
      {actions(
        <Button
          variant="secondary"
          disabled={selected.length === 0 || unmap.isPending || locked}
          title={locked ? "This vocabulary is released and locked" : undefined}
          onClick={onUnmap}
        >
          <Unlink /> Unmap selected {selectedSources.length > 0 && `(${selectedSources.length})`}
        </Button>,
      )}
    </div>
  );
}

export function CommitPage() {
  const { isAdmin } = useAuth();
  const selection = useSelectedVocabulary();
  const vocabularyId = selection.vocabularyId;
  const vocabulary = selection.vocabulary;
  const stats = useMappingStats(vocabularyId);
  const deleteVocabulary = useDeleteVocabulary();

  const onDelete = () => {
    if (!vocabulary) return;
    deleteVocabulary.mutate(vocabulary.id, {
      onSuccess: (result) => {
        toast.success(`Deleted ${result.count} source concepts for vocabulary ID ${vocabulary.id}.`);
        selection.select(null);
      },
      onError: (e) => toast.error(errorMessage(e)),
    });
  };

  const s = stats.data;
  const coveragePct = s && s.total_frequency > 0 ? (s.mapped_frequency / s.total_frequency) * 100 : 0;

  const deleteButton = isAdmin && vocabulary && (
    <ConfirmDialog
      trigger={
        <Button variant="destructive" disabled={deleteVocabulary.isPending}>
          <Trash2 /> Delete source vocabulary
        </Button>
      }
      title={`Delete ${vocabulary.name}?`}
      destructive
      confirmLabel="Delete"
      confirmText={vocabulary.name}
      description={
        <>
          <p>
            This permanently deletes {formatNumber(vocabulary.concept_count)} source concepts of vocabulary ID{" "}
            {vocabulary.id}, together with their mappings, audit trail and suggestions.
          </p>
          {vocabulary.latest_release != null && (
            <p>
              <b>
                Its{" "}
                {vocabulary.latest_release === 1
                  ? "published release"
                  : `${vocabulary.latest_release} published releases`}{" "}
                and their source_to_concept_map history are deleted too.
              </b>
            </p>
          )}
          <p>Download the CSV (and each release's source_to_concept_map) first if you need them.</p>
        </>
      }
      onConfirm={onDelete}
    />
  );

  return (
    <div className="space-y-6">
      <PageHeader
        icon={ClipboardCheck}
        step={4}
        title="Check & commit"
        description="Look over the final mappings, flag doubtful ones for review, publish them as a release and download them as CSV."
      />
      <VocabularySelector selection={selection} releaseNotice={false} />
      <ErrorAlert error={stats.error} title="Could not load mapping statistics" />
      {vocabularyId !== null && <ReleasePanel key={vocabularyId} vocabularyId={vocabularyId} />}

      {vocabularyId !== null && s && s.total_mappings === 0 && (
        <div className="space-y-4">
          <Alert>
            <AlertDescription>No mapped concepts found for this vocabulary.</AlertDescription>
          </Alert>
          {deleteButton}
        </div>
      )}

      {vocabularyId !== null && s && s.total_mappings > 0 && (
        <>
          <h2 className="text-lg font-semibold">Mapped concepts for {vocabulary?.name}</h2>
          <MappedTable
            key={vocabularyId}
            vocabularyId={vocabularyId}
            locked={vocabulary?.locked ?? false}
            actions={(unmapButton) => (
              <div className="flex flex-wrap items-center gap-2">
                {unmapButton}
                <a
                  href={`/api/source-vocabularies/${vocabularyId}/mapped.csv`}
                  download={`vocabulary_${vocabularyId}_mappings.csv`}
                  className={buttonVariants({ variant: "cta" })}
                >
                  <Download /> Download CSV
                </a>
                <a
                  href={`/api/source-vocabularies/${vocabularyId}/source_to_concept_map.csv`}
                  download={`source_to_concept_map_${vocabularyId}.csv`}
                  className={buttonVariants({ variant: "outline" })}
                  title={
                    vocabulary?.latest_release
                      ? `The OMOP SOURCE_TO_CONCEPT_MAP of release ${vocabulary.latest_release}, the official version: load it into your CDM's vocabulary schema`
                      : "The OMOP SOURCE_TO_CONCEPT_MAP table: load it into your CDM's vocabulary schema"
                  }
                >
                  <Download /> OMOP source_to_concept_map
                  {vocabulary?.latest_release ? ` (release ${vocabulary.latest_release})` : ""}
                </a>
              </div>
            )}
          />
          <div className="grid gap-4 sm:grid-cols-3">
            <StatCard label="Total mappings" value={formatNumber(s.total_mappings)} />
            <StatCard label="Unique OMOP concepts" value={formatNumber(s?.unique_concepts)} />
            <StatCard label="Total frequency coverage" value={formatNumber(s?.frequency_coverage)} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Mapping statistics</CardTitle>
              </CardHeader>
              <CardContent>
                <ChartContainer config={chartConfig} className="h-72 w-full">
                  <BarChart data={s?.by_domain ?? []} margin={{ left: 8, right: 8 }}>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="domain_id" tickLine={false} axisLine={false} />
                    <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={40} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar dataKey="count" fill="var(--color-count)" radius={4} />
                  </BarChart>
                </ChartContainer>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Mapping coverage</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <div className="text-muted-foreground text-xs font-medium uppercase">Total frequency coverage</div>
                  <div className="text-3xl font-semibold tabular-nums">{formatNumber(s?.frequency_coverage)}</div>
                </div>
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span>Source frequency mapped</span>
                    <span className="tabular-nums">
                      {formatNumber(s?.mapped_frequency)} / {formatNumber(s?.total_frequency)} ({coveragePct.toFixed(1)}
                      %)
                    </span>
                  </div>
                  <Progress value={coveragePct} />
                </div>
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span>Source concepts mapped</span>
                    <span className="tabular-nums">
                      {formatNumber(s?.mapped)} / {formatNumber(s?.total)} ({(s?.percentage ?? 0).toFixed(1)}%)
                    </span>
                  </div>
                  <Progress value={s?.percentage ?? 0} />
                </div>
              </CardContent>
            </Card>
          </div>
          {deleteButton && (
            <div className="border-destructive/30 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed p-4">
              <div className="text-sm">
                <div className="font-medium">Remove this source vocabulary</div>
                <div className="text-muted-foreground">
                  Deletes its terms, mappings, suggestions, audit trail and releases. Asks you to type its name.
                </div>
              </div>
              {deleteButton}
            </div>
          )}
        </>
      )}
    </div>
  );
}
