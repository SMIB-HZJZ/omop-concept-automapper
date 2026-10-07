import type { ColumnDef } from "@tanstack/react-table";
import { Download, Trash2, Unlink } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useDeleteVocabulary, useMapped, useMappingStats, useUnmap } from "@/api/queries";
import type { MappedRow } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { ErrorAlert, formatNumber, PageHeader, StatCard } from "@/components/common";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable } from "@/components/DataTable";
import { useSelectedVocabulary, VocabularySelector } from "@/components/VocabularySelector";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Progress } from "@/components/ui/progress";

const chartConfig = { count: { label: "Mappings", color: "var(--chart-1)" } } satisfies ChartConfig;

const rowId = (row: MappedRow) => `${row.source_id}:${row.concept_id}`;

const columns: ColumnDef<MappedRow, unknown>[] = [
  { accessorKey: "source_value", header: "Source code", cell: ({ getValue }) => <span className="font-mono text-xs">{String(getValue())}</span> },
  { accessorKey: "source_concept_name", header: "Source concept name" },
  { accessorKey: "concept_name", header: "OMOP concept name" },
  { accessorKey: "concept_id", header: "Concept ID" },
  { accessorKey: "domain_id", header: "Domain" },
  { accessorKey: "freq", header: "Freq", cell: ({ getValue }) => (getValue() as number | null)?.toLocaleString() ?? "—" },
  { accessorKey: "mapped_by", header: "Mapped by", cell: ({ getValue }) => (getValue() as string | null) ?? "—" },
];

export function CommitPage() {
  const { isAdmin } = useAuth();
  const selection = useSelectedVocabulary();
  const vocabularyId = selection.vocabularyId;
  const vocabulary = selection.vocabulary;
  const mapped = useMapped(vocabularyId);
  const stats = useMappingStats(vocabularyId);
  const unmap = useUnmap(vocabularyId);
  const deleteVocabulary = useDeleteVocabulary();
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => setSelected([]), [vocabularyId]);

  const rows = mapped.data ?? [];
  const selectedSources = useMemo(
    () => [...new Set(selected.map((id) => Number(id.split(":")[0])))],
    [selected],
  );

  const onUnmap = () =>
    unmap.mutate(selectedSources, {
      onSuccess: () => {
        toast.success(`Unmapped ${selectedSources.length} concepts`);
        setSelected([]);
      },
      onError: (e) => toast.error(errorMessage(e)),
    });

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
          <p>Download the CSV first if you need the mappings.</p>
        </>
      }
      onConfirm={onDelete}
    />
  );

  return (
    <div className="space-y-6">
      <PageHeader title="Check & Commit Mappings" description="Review the final mappings, export them, or remove ones that are wrong." />
      <VocabularySelector selection={selection} showDrug={false} />
      <ErrorAlert error={mapped.error ?? stats.error} title="Could not load mappings" />

      {vocabularyId !== null && mapped.isSuccess && rows.length === 0 && (
        <div className="space-y-4">
          <Alert>
            <AlertDescription>No mapped concepts found for this vocabulary.</AlertDescription>
          </Alert>
          {deleteButton}
        </div>
      )}

      {vocabularyId !== null && rows.length > 0 && (
        <>
          <h2 className="text-lg font-semibold">Mapped concepts for {vocabulary?.name}</h2>
          <DataTable
            columns={columns}
            data={rows}
            getRowId={rowId}
            selection="multi"
            selected={selected}
            onSelectedChange={setSelected}
            maxHeight="36rem"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" disabled={selected.length === 0 || unmap.isPending} onClick={onUnmap}>
              <Unlink /> Unmap selected {selectedSources.length > 0 && `(${selectedSources.length})`}
            </Button>
            <a
              href={`/api/source-vocabularies/${vocabularyId}/mapped.csv`}
              download={`vocabulary_${vocabularyId}_mappings.csv`}
              className={buttonVariants({ variant: "outline" })}
            >
              <Download /> Download CSV
            </a>
            <div className="ml-auto">{deleteButton}</div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <StatCard label="Total mappings" value={formatNumber(s?.total_mappings ?? rows.length)} />
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
                      {formatNumber(s?.mapped_frequency)} / {formatNumber(s?.total_frequency)} ({coveragePct.toFixed(1)}%)
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
        </>
      )}
    </div>
  );
}
