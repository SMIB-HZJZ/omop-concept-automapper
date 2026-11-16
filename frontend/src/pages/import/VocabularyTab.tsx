import { CheckCircle2, XCircle } from "lucide-react";

import { api, unwrap } from "@/api/client";
import { useVocabularyStatus } from "@/api/queries";
import { taskResult } from "@/api/taskResults";
import type { Task } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ErrorAlert, StatCard } from "@/components/common";
import { TaskProgress } from "@/components/TaskProgress";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useStartTask } from "@/hooks/useStartTask";
import { useLatestTask } from "@/hooks/useTasks";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import { AdminNote } from "./AdminNote";

function VocabularyImportResult({ task }: { task: Task }) {
  const r = taskResult(task, "vocabulary.import");
  if (!r) return null;
  return (
    <div className="space-y-1">
      <div>
        Imported {r.tables_imported} table(s), {formatNumber(r.records_imported)} records. Mappings to concepts that are
        no longer standard: {formatNumber(r.non_standard_mappings_replaced ?? 0)} replaced by their successor (Maps to),{" "}
        {formatNumber(r.non_standard_mappings_removed)} removed (now unmapped).
        {(r.stale_suggestions_removed ?? 0) > 0 &&
          ` Dropped ${formatNumber(r.stale_suggestions_removed)} pending suggestions to such concepts.`}
        {(r.vectors_pruned ?? 0) > 0 &&
          ` Removed ${formatNumber(r.vectors_pruned)} embeddings of concepts that are no longer standard.`}
        {r.drugs_with_atc7 != null && ` ATC7 codes recomputed for ${formatNumber(r.drugs_with_atc7)} drugs.`}
        {r.embedding_refresh &&
          (r.embedding_refresh.changed > 0 || r.embedding_refresh.new > 0) &&
          ` Embeddings: ${formatNumber(r.embedding_refresh.changed)} concepts with changed text, ${formatNumber(
            r.embedding_refresh.new,
          )} new concepts in embedded domains. ${r.embedding_refresh.message ?? ""}`}
      </div>
      <ul className="list-disc pl-5">
        {Object.entries(r.tables).map(([name, t]) => (
          <li key={name}>
            {name}: {t.status}
            {t.status === "success" && ` (${formatNumber(t.records_imported)} records)`}
            {t.error && <span className="text-destructive"> — {t.error}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function VocabularyTab() {
  const { isAdmin } = useAuth();
  const status = useVocabularyStatus();
  const latest = useLatestTask("vocabulary.import");
  const { run, busy } = useStartTask();
  const active = latest.data && !latest.data.is_terminal;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Import OMOP vocabulary tables</CardTitle>
          <CardDescription>
            Put the Athena download zip (no need to unpack it) or its CSV files in the <code>vocabulary/</code>{" "}
            directory: <code>CONCEPT.csv</code>, <code>CONCEPT_RELATIONSHIP.csv</code> and{" "}
            <code>CONCEPT_ANCESTOR.csv</code>, optionally <code>DRUG_STRENGTH.csv</code> and{" "}
            <code>CONCEPT_SYNONYM.csv</code>. With several zips, the newest is used; a CSV next to it is used only if it
            is newer. Concepts are upserted; the other tables are replaced. Mappings to concepts that are no longer
            standard are replaced by their successor or removed. Search and mapping keep working during the import.
          </CardDescription>
        </CardHeader>
      </Card>

      <section className="space-y-3">
        <h3 className="font-medium">Current import status</h3>
        <ErrorAlert error={status.error} />
        <div className="grid gap-4 sm:grid-cols-3">
          {status.data?.tables.map((t) => (
            <StatCard
              key={t.table_name}
              label={t.table_name.replace(/_/g, " ")}
              value={formatNumber(t.row_count)}
              hint={
                <>
                  Last import: {t.last_import ? formatDate(t.last_import) : "never"}
                  {t.last_error && <div className="text-destructive mt-1 break-all">Last error: {t.last_error}</div>}
                </>
              }
            />
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="font-medium">File status</h3>
        <div className="divide-y rounded-md border">
          {status.data?.files.map((f) => (
            <div key={f.table_name} className="flex items-center gap-3 px-4 py-2 text-sm">
              {f.exists ? (
                <CheckCircle2 className="text-success size-4" aria-hidden />
              ) : (
                <XCircle className="text-destructive size-4" aria-hidden />
              )}
              <span className="flex-1 font-medium">
                {f.filename}
                {f.archive && <span className="text-muted-foreground font-normal"> in {f.archive}</span>}
              </span>
              <span className="text-muted-foreground w-24 text-right tabular-nums">
                {f.exists ? formatBytes(f.size) : "Not found"}
              </span>
              <span className="w-36 text-right">{f.exists ? "Ready to import" : "File missing"}</span>
            </div>
          ))}
        </div>
        {status.data && (
          <p className="text-muted-foreground text-xs">
            Files are read from <code>{status.data.vocabulary_dir}</code> (the host's <code>vocabulary/</code> folder).
          </p>
        )}
      </section>

      {isAdmin ? (
        <Button
          disabled={busy || !!active || !status.data?.files.some((f) => f.exists)}
          onClick={() => void run(async () => unwrap(await api.POST("/api/vocabulary/import")), "Vocabulary import")}
        >
          Import OMOP vocabulary
        </Button>
      ) : (
        <AdminNote />
      )}

      {latest.data && <TaskProgress task={latest.data} renderResult={(t) => <VocabularyImportResult task={t} />} />}
    </div>
  );
}
