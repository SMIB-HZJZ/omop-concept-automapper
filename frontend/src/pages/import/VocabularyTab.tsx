import { UploadCloud } from "lucide-react";
import { useEffect, useState } from "react";

import { useAtc7Stats, useVocabularyStatus } from "@/api/queries";
import { taskResult } from "@/api/taskResults";
import type { Task, TaskRef } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ErrorAlert, StatCard } from "@/components/common";
import { TaskProgress } from "@/components/TaskProgress";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { useStartTask } from "@/hooks/useStartTask";
import { useLatestTask } from "@/hooks/useTasks";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import { uploadFile } from "@/lib/upload";
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
            {t.status === "success" &&
              (t.records_updated != null
                ? ` (${formatNumber(t.records_imported)} new, ${formatNumber(t.records_updated)} changed; unchanged concepts are left as they are)`
                : ` (${formatNumber(t.records_imported)} records)`)}
            {t.error && <span className="text-destructive"> — {t.error}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

type Upload = { file: File; loaded: number; total: number; abort: () => void };

export function VocabularyTab() {
  const { isAdmin } = useAuth();
  const status = useVocabularyStatus();
  const atc7 = useAtc7Stats();
  const latest = useLatestTask("vocabulary.import");
  const { run, busy } = useStartTask();
  const active = latest.data && !latest.data.is_terminal;
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<Upload | null>(null);

  const maxBytes = status.data?.max_upload_bytes ?? Infinity;
  const problem = !file
    ? null
    : !file.name.toLowerCase().endsWith(".zip")
      ? "Choose the .zip file as downloaded from Athena."
      : file.size > maxBytes
        ? `The file is ${formatBytes(file.size)}; the limit is ${formatBytes(maxBytes)}.`
        : null;

  // Leaving the page would cancel the upload
  useEffect(() => {
    if (!upload) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [upload]);

  const start = (zip: File) =>
    run(async () => {
      const handle = uploadFile<TaskRef>(
        `/api/vocabulary/import?filename=${encodeURIComponent(zip.name)}`,
        zip,
        (loaded, total) => setUpload((u) => (u ? { ...u, loaded, total } : u)),
      );
      setUpload({ file: zip, loaded: 0, total: zip.size, abort: handle.abort });
      try {
        const ref = await handle.promise;
        setFile(null);
        return ref;
      } finally {
        setUpload(null);
      }
    }, "Vocabulary import");

  const percent = upload ? Math.round((upload.loaded / Math.max(upload.total, 1)) * 100) : 0;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Import OMOP vocabulary tables</CardTitle>
          <CardDescription>
            Upload the vocabulary download from{" "}
            <a href="https://athena.ohdsi.org/" target="_blank" rel="noreferrer" className="underline">
              Athena
            </a>{" "}
            as it is, a <code>.zip</code> (no need to unpack it). It should contain <code>CONCEPT.csv</code>,{" "}
            <code>CONCEPT_RELATIONSHIP.csv</code> and <code>CONCEPT_ANCESTOR.csv</code>, optionally{" "}
            <code>DRUG_STRENGTH.csv</code> and <code>CONCEPT_SYNONYM.csv</code>. Concepts are upserted; the other tables
            are replaced, and the ATC7 codes of drugs are computed. Mappings to concepts that are no longer standard are
            replaced by their successor or removed. Search and mapping keep working during the import.
          </CardDescription>
        </CardHeader>
      </Card>

      <section className="space-y-3">
        <h3 className="font-medium">Current tables</h3>
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
          {atc7.data && (
            <StatCard
              label="drugs with ATC7 codes"
              value={formatNumber(atc7.data.total_drugs_with_atc7)}
              hint="Computed by each import; ATC7 codes narrow drug searches."
            />
          )}
        </div>
      </section>

      {isAdmin ? (
        <section className="space-y-3">
          <h3 className="font-medium">Upload a vocabulary release</h3>
          <div className="grid max-w-xl gap-2">
            <Label htmlFor="vocabulary-zip">Athena download (.zip)</Label>
            <Input
              id="vocabulary-zip"
              type="file"
              accept=".zip,application/zip"
              disabled={!!upload}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            {file && !problem && (
              <p className="text-muted-foreground text-xs">
                {file.name} · {formatBytes(file.size)}
              </p>
            )}
            {problem && <p className="text-destructive text-xs">{problem}</p>}
          </div>

          {upload ? (
            <div className="max-w-xl space-y-2 rounded-xl border p-3">
              <div className="flex items-center justify-between gap-2 text-sm">
                <span className="flex min-w-0 items-center gap-2">
                  <UploadCloud className="text-primary size-4 shrink-0" />
                  <span className="truncate">Uploading {upload.file.name}</span>
                </span>
                <span className="text-muted-foreground shrink-0 tabular-nums">
                  {formatBytes(upload.loaded)} / {formatBytes(upload.total)} · {percent}%
                </span>
              </div>
              <Progress value={percent} />
              <div className="flex items-center justify-between gap-2">
                <p className="text-muted-foreground text-xs">
                  {percent >= 100
                    ? "Checking the zip…"
                    : "Keep this page open until the upload finishes; the import then runs in the background."}
                </p>
                <Button size="sm" variant="ghost" onClick={upload.abort} disabled={percent >= 100}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="cta"
              disabled={!file || !!problem || busy || !!active}
              onClick={() => file && void start(file)}
            >
              Upload and import
            </Button>
          )}
          {active && !upload && (
            <p className="text-muted-foreground text-xs">An import is running; wait for it to finish.</p>
          )}
        </section>
      ) : (
        <AdminNote />
      )}

      {latest.data && <TaskProgress task={latest.data} renderResult={(t) => <VocabularyImportResult task={t} />} />}
    </div>
  );
}
