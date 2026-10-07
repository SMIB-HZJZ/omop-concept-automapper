import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, Info, XCircle } from "lucide-react";
import Papa from "papaparse";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";

import { api, errorMessage, unwrap } from "@/api/client";
import {
  useAtc7Stats,
  useConfig,
  useEmbeddingStatus,
  useMeta,
  useSourceVocabularies,
  useVocabularyStatus,
} from "@/api/queries";
import type { Task } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorAlert, PageHeader, StatCard, formatBytes, formatDate, formatNumber } from "@/components/common";
import { TaskProgress } from "@/components/TaskProgress";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePersistentState } from "@/hooks/usePersistentState";
import { useLatestTask } from "@/hooks/useTasks";

const TABS = ["vocabulary", "source", "atc7", "embeddings"] as const;
type Tab = (typeof TABS)[number];
const REQUIRED_COLUMNS = ["source_value", "source_concept_name"];

/** Starts a task through `start`, refreshes task lists and reports conflicts/errors as toasts. */
function useStartTask() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const run = async (start: () => Promise<unknown>, label: string) => {
    setBusy(true);
    try {
      await start();
      toast.success(`${label} started`);
    } catch (err) {
      toast.error(`Could not start ${label.toLowerCase()}`, { description: errorMessage(err) });
    } finally {
      setBusy(false);
      void qc.invalidateQueries({ queryKey: ["tasks"] });
    }
  };
  return { run, busy };
}

function AdminNote() {
  return (
    <Alert>
      <Info />
      <AlertDescription>Only administrators can run this action.</AlertDescription>
    </Alert>
  );
}

// ----------------------------------------------------------------------------- vocabulary

function VocabularyTab() {
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
            Place your OMOP vocabulary files (from Athena) in the <code>vocabulary/</code> directory: <code>CONCEPT.csv</code>,{" "}
            <code>CONCEPT_RELATIONSHIP.csv</code> and <code>CONCEPT_ANCESTOR.csv</code>, then run the import. Concepts are
            upserted; relationships and ancestors are replaced. Mappings to concepts that are no longer standard are removed
            afterwards.
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
                <CheckCircle2 className="text-success size-4" />
              ) : (
                <XCircle className="text-destructive size-4" />
              )}
              <span className="flex-1 font-medium">{f.filename}</span>
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
          onClick={() =>
            void run(async () => unwrap(await api.POST("/api/vocabulary/import")), "Vocabulary import")
          }
        >
          Import OMOP vocabulary
        </Button>
      ) : (
        <AdminNote />
      )}

      {latest.data && (
        <TaskProgress
          task={latest.data}
          renderResult={(r) => {
            const tables = (r.tables ?? {}) as Record<string, { status: string; records_imported: number; error: string | null }>;
            return (
              <div className="space-y-1">
                <div>
                  Imported {String(r.tables_imported ?? 0)} table(s), {formatNumber(Number(r.records_imported ?? 0))} records.
                  Removed {formatNumber(Number(r.non_standard_mappings_removed ?? 0))} mappings to non-standard concepts.
                </div>
                <ul className="list-disc pl-5">
                  {Object.entries(tables).map(([name, t]) => (
                    <li key={name}>
                      {name}: {t.status}
                      {t.status === "success" && ` (${formatNumber(t.records_imported)} records)`}
                      {t.error && <span className="text-destructive"> — {t.error}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            );
          }}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------- source concepts

type Preview = { columns: string[]; rows: Record<string, string>[] };

function SourceTab() {
  const vocabularies = useSourceVocabularies();
  const latest = useLatestTask("source.import");
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [vocabularyId, setVocabularyId] = useState(1);
  const [name, setName] = useState("");
  const [isDrug, setIsDrug] = useState(false);
  const [busy, setBusy] = useState(false);

  const existing = vocabularies.data?.find((v) => v.id === vocabularyId);
  const missing = preview ? REQUIRED_COLUMNS.filter((c) => !preview.columns.includes(c)) : [];
  const active = latest.data && !latest.data.is_terminal;

  const onFile = (f: File | null) => {
    setFile(f);
    setPreview(null);
    setParseError(null);
    if (!f) return;
    Papa.parse<Record<string, string>>(f, {
      header: true,
      preview: 6,
      skipEmptyLines: true,
      complete: (res) => {
        const columns = (res.meta.fields ?? []).map((c) => c.trim());
        setPreview({ columns, rows: res.data.slice(0, 5) });
      },
      error: (err) => setParseError(err.message),
    });
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      if (name.trim()) fd.append("name", name.trim());
      fd.append("is_drug", isDrug ? "true" : "false");
      const resp = await fetch(`/api/source-vocabularies/${vocabularyId}/concepts/import`, {
        method: "POST",
        body: fd,
        credentials: "include",
      });
      if (!resp.ok) {
        const body: unknown = await resp.json().catch(() => resp.statusText);
        throw new Error(errorMessage(body));
      }
      toast.success("Source concept import started");
    } catch (err) {
      toast.error("Could not import source concepts", { description: errorMessage(err) });
    } finally {
      setBusy(false);
      void qc.invalidateQueries({ queryKey: ["tasks"] });
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Import source concepts</CardTitle>
          <CardDescription>
            <div className="space-y-2">
              <p>
                Upload a CSV file containing your source concepts that need to be mapped to OMOP. The file should be comma
                delimited, not tab (like OMOP vocabularies).
              </p>
              <p>
                Required columns: <code>source_value</code> (source concept code/value), <code>source_concept_name</code>{" "}
                (name or description of the source concept).
              </p>
              <p>
                Optional columns: <code>freq</code> (frequency/count of usage; empty means 1), <code>concept_id</code> (OMOP
                concept ID(s) for existing mappings; separate multiple IDs with semicolons, e.g. "1234;5678;9012").
              </p>
            </div>
          </CardDescription>
        </CardHeader>
      </Card>

      <Alert>
        <Info />
        <AlertTitle>Drug-specific mapping enhancement</AlertTitle>
        <AlertDescription>
          For drug vocabularies, if your <code>source_value</code> starts with an ATC7 code (e.g. 'A10BA02XYZ'), the system
          uses this code to limit the search space during drug-specific mapping.
        </AlertDescription>
      </Alert>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor="source-file">Source concepts CSV</Label>
          <Input id="source-file" type="file" accept=".csv,text/csv" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="source-vocab-id">Source vocabulary ID</Label>
          <Input
            id="source-vocab-id"
            type="number"
            min={1}
            value={vocabularyId}
            onChange={(e) => setVocabularyId(Math.max(1, Number(e.target.value) || 1))}
          />
          <p className="text-muted-foreground text-xs">
            {existing
              ? `Vocabulary ${existing.id} ("${existing.name}") already exists with ${formatNumber(existing.concept_count)} concepts — the import appends to it.`
              : vocabularies.data && vocabularies.data.length > 0
                ? `Existing IDs: ${vocabularies.data.map((v) => v.id).join(", ")}. A new ID creates a new vocabulary.`
                : "Unique identifier for this source vocabulary."}
          </p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="source-vocab-name">Name (optional)</Label>
          <Input id="source-vocab-name" value={name} placeholder={`Vocabulary ${vocabularyId}`} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="flex items-center gap-2 pt-6">
          <Checkbox id="source-is-drug" checked={isDrug} onCheckedChange={(v) => setIsDrug(!!v)} />
          <Label htmlFor="source-is-drug">Drug vocabulary</Label>
        </div>
      </div>

      {parseError && <ErrorAlert error={parseError} title="Could not read the file" />}
      {missing.length > 0 && (
        <ErrorAlert error={`Missing required columns: ${missing.join(", ")}`} title="Invalid CSV" />
      )}

      {preview && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Checkbox id="show-preview" checked={showPreview} onCheckedChange={(v) => setShowPreview(!!v)} />
            <Label htmlFor="show-preview">Preview source data</Label>
          </div>
          {showPreview && (
            <div className="overflow-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    {preview.columns.map((c) => (
                      <TableHead key={c}>{c}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.rows.map((row, i) => (
                    <TableRow key={i}>
                      {preview.columns.map((c) => (
                        <TableCell key={c}>{row[c]}</TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      )}

      <Button disabled={!file || !preview || missing.length > 0 || busy || !!active} onClick={() => void submit()}>
        Import source concepts
      </Button>

      {latest.data && (
        <TaskProgress
          task={latest.data}
          renderResult={(r) => {
            const invalid = (r.invalid_concept_ids ?? []) as number[];
            return (
              <div className="space-y-1">
                <div>
                  Imported {formatNumber(Number(r.inserted ?? 0))} source concepts into vocabulary {String(r.vocabulary_id ?? "")}
                  {r.filename ? ` from ${String(r.filename)}` : ""}.
                </div>
                <div>
                  {formatNumber(Number(r.premapped_sources ?? 0))} concepts came with existing mappings (
                  {formatNumber(Number(r.premapped_links ?? 0))} links).
                </div>
                {invalid.length > 0 && (
                  <div className="text-destructive">
                    Unknown concept IDs ignored: {invalid.slice(0, 20).join(", ")}
                    {invalid.length > 20 && ` … and ${invalid.length - 20} more`}
                  </div>
                )}
                <Link to="/map" className="underline">
                  Start mapping →
                </Link>
              </div>
            );
          }}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------- ATC7

function Atc7Tab() {
  const { isAdmin } = useAuth();
  const stats = useAtc7Stats();
  const latest = useLatestTask("atc7.process");
  const { run, busy } = useStartTask();
  const active = latest.data && !latest.data.is_terminal;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>ATC7 code processing</CardTitle>
          <CardDescription>
            After importing the OMOP vocabulary tables, process ATC7 codes for drug concepts. This finds all ATC7 codes that
            drug concepts belong to through relationships and hierarchies.
          </CardDescription>
        </CardHeader>
      </Card>
      <Alert>
        <Info />
        <AlertTitle>Prerequisites</AlertTitle>
        <AlertDescription>Import the CONCEPT, CONCEPT_RELATIONSHIP and CONCEPT_ANCESTOR tables first.</AlertDescription>
      </Alert>

      {isAdmin ? (
        <Button
          disabled={busy || !!active}
          onClick={() => void run(async () => unwrap(await api.POST("/api/atc7/process")), "ATC7 processing")}
        >
          Process ATC7 codes
        </Button>
      ) : (
        <AdminNote />
      )}

      {latest.data && (
        <TaskProgress
          task={latest.data}
          renderResult={(r) =>
            Number(r.drugs_with_atc7 ?? 0) > 0
              ? `Processed ATC7 codes for ${formatNumber(Number(r.drugs_with_atc7))} drug concepts.`
              : "No ATC7 codes found. Ensure all vocabulary tables are imported."
          }
        />
      )}

      <section className="space-y-3">
        <h3 className="font-medium">ATC7 statistics</h3>
        <ErrorAlert error={stats.error} />
        {stats.data && stats.data.total_drugs_with_atc7 === 0 ? (
          <Alert>
            <Info />
            <AlertDescription>No ATC7 data found. Process ATC7 codes first.</AlertDescription>
          </Alert>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <StatCard label="Drugs with ATC7 codes" value={formatNumber(stats.data?.total_drugs_with_atc7)} />
            <StatCard label="Average ATC7 codes per drug" value={(stats.data?.avg_atc7_per_drug ?? 0).toFixed(1)} />
          </div>
        )}
      </section>
    </div>
  );
}

// ----------------------------------------------------------------------------- embeddings

const EMBEDDING_KINDS = [
  "embeddings.embed",
  "embeddings.sync_metadata",
  "embeddings.sync_atc7",
  "embeddings.migrate_qdrant",
] as const;

function renderEmbeddingResult(task: Task) {
  return (r: Record<string, unknown>) => {
    switch (task.kind) {
      case "embeddings.embed":
        return r.message
          ? String(r.message)
          : `Embedded ${formatNumber(Number(r.embedded ?? 0))} concepts into ${String(r.collection ?? "")}.`;
      case "embeddings.sync_metadata":
        return `Removed ${formatNumber(Number(r.removed ?? 0))} non-standard concepts, refreshed ${formatNumber(
          Number(r.relabeled ?? 0),
        )} domain labels. ${String(r.message ?? "")}`;
      case "embeddings.sync_atc7":
        return `${formatNumber(Number(r.embedded_with_atc7 ?? 0))} embedded concepts have ATC7 codes. ${String(r.message ?? "")}`;
      default:
        return `Copied ${formatNumber(Number(r.copied ?? 0))} of ${formatNumber(Number(r.points_in_qdrant ?? 0))} vectors from Qdrant into ${String(
          r.collection ?? "",
        )} (removed ${formatNumber(Number(r.removed_non_standard ?? 0))} non-standard).`;
    }
  };
}

function EmbeddingsTab() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const config = useConfig();
  const meta = useMeta();
  const [domain, setDomain] = useState<string | null>(null);
  const status = useEmbeddingStatus(domain);
  const [batchSize, setBatchSize] = usePersistentState("embed-batch-size", 1000);
  const [qdrantUrl, setQdrantUrl] = useState("http://qdrant:6333");
  const [qdrantCollection, setQdrantCollection] = useState("omop_vocab");
  const { run, busy } = useStartTask();

  const t0 = useLatestTask(EMBEDDING_KINDS[0]);
  const t1 = useLatestTask(EMBEDDING_KINDS[1]);
  const t2 = useLatestTask(EMBEDDING_KINDS[2]);
  const t3 = useLatestTask(EMBEDDING_KINDS[3]);
  const latest = useMemo(() => {
    const tasks = [t0.data, t1.data, t2.data, t3.data].filter((t): t is Task => !!t);
    return tasks.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))[0] ?? null;
  }, [t0.data, t1.data, t2.data, t3.data]);
  const anyActive = [t0.data, t1.data, t2.data, t3.data].some((t) => t && !t.is_terminal);
  const disabled = busy || anyActive;

  const clear = async () => {
    try {
      const res = unwrap(await api.DELETE("/api/embeddings"));
      toast.success(res.message);
    } catch (err) {
      toast.error("Could not clear embeddings", { description: errorMessage(err) });
    } finally {
      void qc.invalidateQueries({ queryKey: ["embedding-status"] });
    }
  };

  const s = status.data;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Embedding management</CardTitle>
          <CardDescription>Embed standard concepts into the vector store to enable similarity search and auto-mapping.</CardDescription>
        </CardHeader>
        <CardContent>
          {config.data && (
            <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
              <dt className="text-muted-foreground">Collection</dt>
              <dd><code>{config.data.collection_name}</code></dd>
              <dt className="text-muted-foreground">Embedding model</dt>
              <dd><code>{config.data.embedding_model}</code></dd>
              <dt className="text-muted-foreground">Dimensions</dt>
              <dd><code>{config.data.embedding_dims ?? "native"}</code></dd>
              <dt className="text-muted-foreground">LLM model</dt>
              <dd><code>{config.data.llm_model}</code></dd>
            </dl>
          )}
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h3 className="font-medium">Standard concepts</h3>
          <div className="grid gap-1.5">
            <Label htmlFor="embed-domain">Filter by domain (optional)</Label>
            <Select value={domain ?? "__all"} onValueChange={(v) => setDomain(v === "__all" ? null : v)}>
              <SelectTrigger id="embed-domain" className="w-52">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all">All</SelectItem>
                {meta.data?.domains.map((d) => (
                  <SelectItem key={d} value={d}>
                    {d}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <ErrorAlert error={status.error} />
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Points in collection" value={formatNumber(s?.points_count)} hint={s && !s.exists ? "Collection not created yet" : undefined} />
          <StatCard label="Total standard concepts" value={formatNumber(s?.total)} hint={domain ? `Domain: ${domain}` : "All domains"} />
          <StatCard label="Embedded" value={formatNumber(s?.embedded)} hint={`${formatNumber(s?.pending)} pending`} />
        </div>
        <div className="space-y-1">
          <Progress value={s?.percentage ?? 0} />
          <div className="text-muted-foreground text-xs">
            {(s?.percentage ?? 0).toFixed(1)}% embedded · boxed drugs and marketed products are excluded
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <div className="grid max-w-md gap-2">
          <Label>Batch size: {batchSize}</Label>
          <Slider min={100} max={2000} step={10} value={[batchSize]} onValueChange={(v) => setBatchSize(v[0] ?? 1000)} />
          <p className="text-muted-foreground text-xs">Number of concepts sent to the embedding model per request.</p>
        </div>
        {isAdmin ? (
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={disabled}
              onClick={() =>
                void run(
                  async () => unwrap(await api.POST("/api/embeddings/embed", { body: { domain, batch_size: batchSize } })),
                  "Embedding",
                )
              }
            >
              Embed standard concepts
            </Button>
            <Button
              variant="outline"
              disabled={disabled}
              onClick={() => void run(async () => unwrap(await api.POST("/api/embeddings/sync-metadata")), "Metadata sync")}
            >
              Sync standard concepts metadata
            </Button>
            <Button
              variant="outline"
              disabled={disabled}
              onClick={() => void run(async () => unwrap(await api.POST("/api/embeddings/sync-atc7")), "ATC7 sync")}
            >
              Sync ATC7 codes only
            </Button>
          </div>
        ) : (
          <AdminNote />
        )}
        <p className="text-muted-foreground text-xs">
          Concept names, classes and ATC7 codes are joined live from the vocabulary tables at search time, so the syncs only
          remove vectors of concepts that are no longer standard, refresh domain labels, and report ATC7 coverage.
        </p>
      </section>

      {latest && <TaskProgress task={latest} renderResult={renderEmbeddingResult(latest)} />}

      {isAdmin && (
        <Collapsible>
          <Card>
            <CardHeader>
              <CollapsibleTrigger className="flex w-full items-center justify-between text-left">
                <div>
                  <CardTitle>Migrate from Qdrant</CardTitle>
                  <CardDescription>Copy vectors from the old Qdrant collection so nothing has to be re-embedded.</CardDescription>
                </div>
                <ChevronDown className="size-4" />
              </CollapsibleTrigger>
            </CardHeader>
            <CollapsibleContent>
              <CardContent className="space-y-4">
                <p className="text-muted-foreground text-sm">
                  Start the old vector store first with <code>docker compose --profile qdrant-migration up -d qdrant</code>. The
                  current embedding model and dimensions (Configuration) must match those used to create the Qdrant collection.
                </p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label htmlFor="qdrant-url">Qdrant URL</Label>
                    <Input id="qdrant-url" value={qdrantUrl} onChange={(e) => setQdrantUrl(e.target.value)} />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="qdrant-collection">Collection</Label>
                    <Input id="qdrant-collection" value={qdrantCollection} onChange={(e) => setQdrantCollection(e.target.value)} />
                  </div>
                </div>
                <Button
                  disabled={disabled || !qdrantUrl || !qdrantCollection}
                  onClick={() =>
                    void run(
                      async () =>
                        unwrap(
                          await api.POST("/api/embeddings/migrate-from-qdrant", {
                            body: { qdrant_url: qdrantUrl, collection: qdrantCollection, batch_size: 1000 },
                          }),
                        ),
                      "Qdrant migration",
                    )
                  }
                >
                  Start migration
                </Button>
              </CardContent>
            </CollapsibleContent>
          </Card>
        </Collapsible>
      )}

      {isAdmin && config.data && (
        <Card className="border-destructive/40">
          <CardHeader>
            <CardTitle className="text-destructive">Clear embeddings</CardTitle>
            <CardDescription>
              Removes all embeddings in the current collection <code>{config.data.collection_name}</code>. Search and
              auto-mapping stop working until concepts are embedded again.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ConfirmDialog
              trigger={
                <Button variant="destructive" disabled={anyActive || !s?.exists}>
                  Clear all embeddings
                </Button>
              }
              title="Clear all embeddings?"
              description={<p>All vectors in {config.data.collection_name} will be deleted. This cannot be undone.</p>}
              confirmLabel="Delete embeddings"
              destructive
              confirmText={config.data.collection_name}
              onConfirm={clear}
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------- page

export function ImportPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(tabParam ?? "") ? (tabParam as Tab) : "vocabulary";

  return (
    <div>
      <PageHeader
        title="Data Import & Embedding"
        description="Import OMOP standard vocabularies and your source concepts, process ATC7 codes, and embed concepts for similarity search."
      />
      <Tabs
        value={tab}
        onValueChange={(v) => {
          const next = new URLSearchParams(params);
          next.set("tab", v);
          setParams(next, { replace: true });
        }}
      >
        <TabsList className="mb-4 flex-wrap">
          <TabsTrigger value="vocabulary">OMOP Vocabulary Tables</TabsTrigger>
          <TabsTrigger value="source">Source Concepts</TabsTrigger>
          <TabsTrigger value="atc7">ATC7 Processing</TabsTrigger>
          <TabsTrigger value="embeddings">Embedding Management</TabsTrigger>
        </TabsList>
        <TabsContent value="vocabulary">
          <VocabularyTab />
        </TabsContent>
        <TabsContent value="source">
          <SourceTab />
        </TabsContent>
        <TabsContent value="atc7">
          <Atc7Tab />
        </TabsContent>
        <TabsContent value="embeddings">
          <EmbeddingsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
