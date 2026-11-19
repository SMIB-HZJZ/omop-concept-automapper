import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { api, errorMessage, unwrap } from "@/api/client";
import { useConfig, useEmbeddingStatus, useMeta } from "@/api/queries";
import { taskResult } from "@/api/taskResults";
import type { Task } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorAlert, StatCard } from "@/components/common";
import { MultiSelect } from "@/components/MultiSelect";
import { TaskProgress } from "@/components/TaskProgress";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { useStartTask } from "@/hooks/useStartTask";
import { useLatestTask } from "@/hooks/useTasks";
import { formatNumber } from "@/lib/format";
import { AdminNote } from "./AdminNote";

function EmbeddingResult({ task }: { task: Task }) {
  const embed = taskResult(task, "embeddings.embed");
  if (embed) {
    return embed.message ?? `Embedded ${formatNumber(embed.embedded)} concepts into ${embed.collection}.`;
  }
  const sync = taskResult(task, "embeddings.sync_metadata");
  if (sync) {
    return `Removed ${formatNumber(sync.removed)} non-standard concepts, refreshed ${formatNumber(sync.relabeled)} domain labels. ${sync.message ?? ""}`;
  }
  const atc7 = taskResult(task, "embeddings.sync_atc7");
  if (atc7) return `${formatNumber(atc7.embedded_with_atc7)} embedded concepts have ATC7 codes. ${atc7.message ?? ""}`;
  return null;
}

function newest(tasks: (Task | null | undefined)[]): Task | null {
  return (
    tasks.filter((t): t is Task => !!t).sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))[0] ??
    null
  );
}

function ConfigSummary() {
  const config = useConfig();
  if (!config.data) return null;
  return (
    <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
      <dt className="text-muted-foreground">Collection</dt>
      <dd>
        <code>{config.data.collection_name}</code>
      </dd>
      <dt className="text-muted-foreground">Embedding model</dt>
      <dd>
        <code>{config.data.embedding_model}</code>
      </dd>
      <dt className="text-muted-foreground">Dimensions</dt>
      <dd>
        <code>{config.data.embedding_dims ?? "native"}</code>
      </dd>
      <dt className="text-muted-foreground">LLM model</dt>
      <dd>
        <code>{config.data.llm_model}</code>
      </dd>
    </dl>
  );
}

function ClearEmbeddings({ collection, disabled }: { collection: string; disabled: boolean }) {
  const qc = useQueryClient();
  const clear = async () => {
    try {
      const res = unwrap(await api.DELETE("/api/embeddings", { params: { query: { confirm: collection } } }));
      toast.success(res.message);
    } catch (err) {
      toast.error("Could not clear embeddings", { description: errorMessage(err) });
    } finally {
      void qc.invalidateQueries({ queryKey: ["embedding-status"] });
    }
  };
  return (
    <Card className="border-destructive/40">
      <CardHeader>
        <CardTitle className="text-destructive">Clear embeddings</CardTitle>
        <CardDescription>
          Removes all embeddings in the current collection <code>{collection}</code>. Search and auto-mapping stop
          working until concepts are embedded again.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ConfirmDialog
          trigger={
            <Button variant="destructive" disabled={disabled}>
              Clear all embeddings
            </Button>
          }
          title="Clear all embeddings?"
          description={<p>All vectors in {collection} will be deleted. This cannot be undone.</p>}
          confirmLabel="Delete embeddings"
          destructive
          confirmText={collection}
          onConfirm={clear}
        />
      </CardContent>
    </Card>
  );
}

export function EmbeddingsTab() {
  const { isAdmin } = useAuth();
  const config = useConfig();
  const meta = useMeta();
  // Several domains embed in one run, so the search index is rebuilt once
  const [domains, setDomains] = useState<string[]>([]);
  const status = useEmbeddingStatus(domains);
  const { run, busy } = useStartTask();

  const embed = useLatestTask("embeddings.embed");
  const syncMetadata = useLatestTask("embeddings.sync_metadata");
  const syncAtc7 = useLatestTask("embeddings.sync_atc7");
  const tasks = [embed.data, syncMetadata.data, syncAtc7.data];
  const latest = newest(tasks);
  const anyActive = tasks.some((t) => t && !t.is_terminal);
  const disabled = busy || anyActive;

  const s = status.data;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Embedding management</CardTitle>
          <CardDescription>
            Embed standard concepts into the vector store to enable similarity search and auto-mapping.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ConfigSummary />
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h3 className="font-medium">Standard concepts</h3>
          <div className="grid gap-1.5">
            <Label htmlFor="embed-domains">Domains (counts and embedding)</Label>
            <MultiSelect
              id="embed-domains"
              className="w-72"
              options={meta.data?.domains ?? []}
              value={domains}
              onChange={setDomains}
              placeholder="All domains"
            />
          </div>
        </div>
        <ErrorAlert error={status.error} />
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard
            label="Points in collection"
            value={formatNumber(s?.points_count)}
            hint={s && !s.exists ? "Collection not created yet" : undefined}
          />
          <StatCard
            label="Total standard concepts"
            value={formatNumber(s?.total)}
            hint={domains.length ? domains.join(", ") : "All domains"}
          />
          <StatCard
            label="Embedded"
            value={formatNumber(s?.embedded)}
            hint={
              (s?.changed ?? 0) > 0
                ? `${formatNumber(s?.pending)} pending · ${formatNumber(s?.changed)} changed in a vocabulary update`
                : `${formatNumber(s?.pending)} pending`
            }
          />
        </div>
        <div className="space-y-1">
          <Progress value={s?.percentage ?? 0} aria-label="Embedded standard concepts" />
          <div className="text-muted-foreground text-xs">
            {(s?.percentage ?? 0).toFixed(1)}% embedded · boxed drugs and marketed products are excluded
          </div>
        </div>
      </section>

      <section className="space-y-3">
        {isAdmin ? (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="cta"
              disabled={disabled}
              onClick={() =>
                void run(
                  async () => unwrap(await api.POST("/api/embeddings/embed", { body: { domains, batch_size: 4000 } })),
                  "Embedding",
                )
              }
            >
              {domains.length
                ? `Embed ${domains.length === 1 ? domains[0] : `${domains.length} domains`}`
                : "Embed all standard concepts"}
            </Button>
            <Button
              variant="outline"
              disabled={disabled}
              onClick={() =>
                void run(async () => unwrap(await api.POST("/api/embeddings/sync-metadata")), "Metadata sync")
              }
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
          Concept names, classes and ATC7 codes are joined live from the vocabulary tables at search time, so the syncs
          only remove vectors of concepts that are no longer standard, refresh domain labels, and report ATC7 coverage.
        </p>
      </section>

      {latest && <TaskProgress task={latest} renderResult={(t) => <EmbeddingResult task={t} />} />}

      {isAdmin && config.data && (
        <ClearEmbeddings collection={config.data.collection_name} disabled={anyActive || !s?.exists} />
      )}
    </div>
  );
}
