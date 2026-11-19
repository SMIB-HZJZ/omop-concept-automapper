import { AlertTriangle, Info, RotateCcw, Save, Settings as SettingsIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import {
  useCollectionPreview,
  useConfig,
  useModelOptions,
  useResetConfig,
  useUpdateConfig,
  useWorkflows,
} from "@/api/queries";
import type { Config, ConfigUpdate, WorkflowCatalog } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ErrorAlert, PageHeader } from "@/components/common";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useDebounce } from "@/hooks/useDebounce";

const CUSTOM = "__custom";
const NATIVE = "native";

const VALUE_LABELS: Record<string, string> = {
  "llm.model": "LLM model",
  "embedding.model": "Embedding model",
  "embedding.dims": "Dimensions",
  "embedding.text": "Embedded text",
  "embedding.task_types": "Query/document task types",
};

const EMBEDDING_TEXTS: { value: string; label: string }[] = [
  { value: "name", label: "Name only" },
  { value: "name_context", label: "Name + class and domain" },
  { value: "name_synonyms", label: "Name + synonyms" },
  { value: "name_context_synonyms", label: "Name + synonyms + class and domain" },
];

function CurrentConfig({ config }: { config: Config }) {
  const stale = config.values.filter((v) => v.stale_override);
  const rows: [string, string, string | null][] = [
    ["LLM model", config.llm_model, "llm.model"],
    ["Embedding model", config.embedding_model, "embedding.model"],
    ["Dimensions", config.embedding_dims ? String(config.embedding_dims) : "native", "embedding.dims"],
    [
      "Embedded text",
      EMBEDDING_TEXTS.find((t) => t.value === config.embedding_text)?.label ?? config.embedding_text,
      "embedding.text",
    ],
    ["Task types", config.embedding_task_types ? "on" : "off", "embedding.task_types"],
    ["Embedding collection", config.collection_name, null],
    ["LLM gateway", config.gateway_url, null],
    ["Default pipeline", config.automap_pipeline, null],
  ];
  const source = (key: string | null) => config.values.find((v) => v.key === key)?.source;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Current configuration</CardTitle>
        <CardDescription>Defaults come from environment variables; admins can override them here.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
          {rows.map(([label, value, key]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="flex flex-wrap items-center gap-2">
                <code className="bg-muted rounded px-1.5 py-0.5 text-xs break-all">{value}</code>
                {key && source(key) && (
                  <Badge variant={source(key) === "override" ? "default" : "outline"}>{source(key)}</Badge>
                )}
              </dd>
            </div>
          ))}
        </dl>
        {stale.map((v) => (
          <Alert key={v.key}>
            <AlertTriangle />
            <AlertTitle>Stale override for {VALUE_LABELS[v.key] ?? v.key}</AlertTitle>
            <AlertDescription>
              An override {v.stale_override} made for a previous provider is ignored. Reset to clear it.
            </AlertDescription>
          </Alert>
        ))}
      </CardContent>
    </Card>
  );
}

/** A Select of known options plus a free-text "Custom" entry. */
function ModelPicker({
  id,
  label,
  options,
  value,
  onChange,
  help,
}: {
  id: string;
  label: string;
  options: string[];
  value: string;
  onChange: (value: string) => void;
  help: string;
}) {
  // "Custom" when chosen explicitly, or when the current value is not one of the (loaded) options
  const [customChosen, setCustom] = useState(false);
  const custom = customChosen || (options.length > 0 && !options.includes(value));
  return (
    <div className="grid content-start gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select
        value={custom ? CUSTOM : value}
        onValueChange={(v) => {
          if (v === CUSTOM) {
            setCustom(true);
          } else {
            setCustom(false);
            onChange(v);
          }
        }}
      >
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
          <SelectItem value={CUSTOM}>Custom model…</SelectItem>
        </SelectContent>
      </Select>
      {custom && (
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value.trim())}
          placeholder="provider/model, e.g. anthropic/claude-sonnet-5"
          aria-label={`${label} (custom)`}
        />
      )}
      <p className="text-muted-foreground text-xs">{help}</p>
    </div>
  );
}

function UpdateForm({ config }: { config: Config }) {
  const options = useModelOptions();
  const update = useUpdateConfig();
  const reset = useResetConfig();
  const [llm, setLlm] = useState(config.llm_model);
  const [embedding, setEmbedding] = useState(config.embedding_model);
  const [dims, setDims] = useState<number | null>(config.embedding_dims ?? null);
  const [text, setText] = useState(config.embedding_text ?? "name");
  const [taskTypes, setTaskTypes] = useState(config.embedding_task_types ?? false);
  const [confirmed, setConfirmed] = useState(false);

  const embeddingOptions = options.data?.embedding_models ?? [];
  const maxDims = embeddingOptions.find((m) => m.name === embedding)?.max_dims ?? null;
  const dimensionOptions = options.data?.dimension_options ?? [512, 1024, 1536, 2048, 3072];
  const llmChanged = llm !== config.llm_model;
  const embeddingChanged =
    embedding !== config.embedding_model ||
    dims !== (config.embedding_dims ?? null) ||
    text !== (config.embedding_text ?? "name") ||
    taskTypes !== (config.embedding_task_types ?? false);
  // Debounced: typing a custom model name would otherwise request a preview per keystroke
  const previewModel = useDebounce(embedding, 400);
  const preview = useCollectionPreview(
    previewModel,
    dims,
    text,
    taskTypes,
    embeddingChanged && previewModel.length > 0,
  );
  const canSave = (llmChanged || embeddingChanged) && llm && embedding && (!embeddingChanged || confirmed);

  const save = () => {
    const body: ConfigUpdate = {};
    if (llmChanged) body.llm_model = llm;
    if (embedding !== config.embedding_model) body.embedding_model = embedding;
    if (dims !== (config.embedding_dims ?? null)) body.embedding_dims = dims;
    if (text !== (config.embedding_text ?? "name")) body.embedding_text = text as ConfigUpdate["embedding_text"];
    if (taskTypes !== (config.embedding_task_types ?? false)) body.embedding_task_types = taskTypes;
    update.mutate(body, {
      onSuccess: () => {
        toast.success("Configuration updated successfully");
        if (embeddingChanged) {
          toast.info("Next steps", {
            description: "Go to Import Data → Embedding Management and embed standard concepts for the new collection.",
            duration: 10_000,
          });
        }
      },
      onError: (e) => toast.error(errorMessage(e)),
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Update configuration</CardTitle>
        <CardDescription>
          Model names use LiteLLM's <code>provider/model</code> form.{" "}
          {options.data && !options.data.gateway_models_available && (
            <span className="text-muted-foreground">Model list from env (gateway unreachable).</span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid items-start gap-6 md:grid-cols-2">
          <ModelPicker
            id="llm-model"
            label="LLM model"
            options={options.data?.llm_models ?? []}
            value={llm}
            onChange={setLlm}
            help="Used for concept reranking, the mapping agent and AI suggestions."
          />
          <div className="space-y-4">
            <ModelPicker
              id="embedding-model"
              label="Embedding model"
              options={embeddingOptions.map((m) => m.name)}
              value={embedding}
              onChange={setEmbedding}
              help="Used to create vector embeddings for similarity search."
            />
            <div className="grid gap-1.5">
              <Label htmlFor="embedding-dims">Dimensions</Label>
              <Select
                value={dims === null ? NATIVE : String(dims)}
                onValueChange={(v) => setDims(v === NATIVE ? null : Number(v))}
              >
                <SelectTrigger id="embedding-dims" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NATIVE}>Native size</SelectItem>
                  {dimensionOptions.map((d) => (
                    <SelectItem key={d} value={String(d)} disabled={maxDims !== null && d > maxDims}>
                      {d}
                      {maxDims !== null && d > maxDims ? ` (max ${maxDims})` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                Higher dimensions can be more accurate but use more storage and are slower.
              </p>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="embedding-text">Embedded text</Label>
              <Select value={text} onValueChange={setText}>
                <SelectTrigger id="embedding-text" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EMBEDDING_TEXTS.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                Synonyms need CONCEPT_SYNONYM.csv. Each choice is its own collection.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox id="embedding-task-types" checked={taskTypes} onCheckedChange={(v) => setTaskTypes(!!v)} />
              <Label htmlFor="embedding-task-types">Query/document task types (Gemini, Voyage, Cohere)</Label>
            </div>
          </div>
        </div>

        {embeddingChanged && (
          <Alert>
            <AlertTriangle />
            <AlertTitle>Changing the embedding configuration</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-5">
                <li>switches to another embedding collection</li>
                <li>requires embedding all concepts again (time and cost), unless the collection already exists</li>
                <li>keeps the previous embeddings, but they are no longer used</li>
              </ul>
              <div className="mt-2">
                New collection: <code className="font-semibold">{preview.data?.name ?? "…"}</code>{" "}
                {preview.data &&
                  (preview.data.exists ? (
                    <Badge variant="secondary">
                      already exists with {(preview.data.embedded_count ?? 0).toLocaleString()} embedded concepts
                    </Badge>
                  ) : (
                    <Badge variant="outline">new — needs embedding</Badge>
                  ))}
              </div>
              <div className="mt-3 flex items-center gap-2">
                <Checkbox id="confirm-embedding" checked={confirmed} onCheckedChange={(v) => setConfirmed(!!v)} />
                <Label htmlFor="confirm-embedding">
                  I understand that changing embedding settings requires re-embedding all concepts
                </Label>
              </div>
            </AlertDescription>
          </Alert>
        )}

        <div className="flex flex-wrap gap-2">
          <Button variant="cta" onClick={save} disabled={!canSave || update.isPending}>
            <Save /> Save configuration
          </Button>
          <ConfirmDialog
            trigger={
              <Button variant="outline" disabled={reset.isPending}>
                <RotateCcw /> Reset to defaults
              </Button>
            }
            title="Reset to defaults?"
            description="All overrides are removed and the values from the environment (.env) are used again."
            confirmLabel="Reset"
            onConfirm={() =>
              reset.mutate(undefined, {
                onSuccess: () => toast.success("Configuration reset to the environment defaults"),
                onError: (e) => toast.error(errorMessage(e)),
              })
            }
          />
        </div>
      </CardContent>
    </Card>
  );
}

function Pipelines({ catalog }: { catalog: WorkflowCatalog }) {
  const errors = Object.entries(catalog.errors);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Mapping pipelines</CardTitle>
        <CardDescription>
          Pipelines are declarative YAML files that wire registered steps together. Extra steps and pipelines are loaded
          from plugins configured with <code>WORKFLOW_PLUGINS</code> and <code>WORKFLOW_DEFINITIONS_DIR</code> (env);
          the default is <code>AUTOMAP_PIPELINE</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {errors.length > 0 && (
          <Alert variant="destructive">
            <AlertTriangle />
            <AlertTitle>Some pipelines or plugins could not be loaded</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-5">
                {errors.map(([source, message]) => (
                  <li key={source} className="break-all">
                    <code>{source}</code>: {message}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
        <div className="grid gap-4 lg:grid-cols-2">
          {catalog.pipelines.map((p) => (
            <div key={p.name} className="rounded-lg border p-4">
              <div className="flex items-center gap-2">
                <span className="font-medium">{p.name}</span>
                {p.is_default && <Badge>default</Badge>}
                {p.source !== "builtin" && <Badge variant="outline">custom</Badge>}
              </div>
              {p.description && <p className="text-muted-foreground mt-1 text-sm">{p.description}</p>}
              <ol className="mt-3 space-y-1 text-sm">
                {Object.entries(p.nodes as Record<string, { uses: string; routes?: Record<string, string> }>).map(
                  ([nodeId, ref]) => {
                    const next = p.edges.filter((e) => e[0] === nodeId).map((e) => e[1]);
                    return (
                      <li key={nodeId} className="flex flex-wrap items-center gap-1">
                        <code className="bg-muted rounded px-1.5 py-0.5 text-xs">{nodeId}</code>
                        <span className="text-muted-foreground text-xs">uses {ref.uses}</span>
                        {nodeId === p.entry && <Badge variant="secondary">entry</Badge>}
                        {ref.routes
                          ? Object.entries(ref.routes).map(([label, target]) => (
                              <span key={label} className="text-muted-foreground text-xs">
                                · {label} → {target}
                              </span>
                            ))
                          : next.map((target) => (
                              <span key={target} className="text-muted-foreground text-xs">
                                → {target}
                              </span>
                            ))}
                      </li>
                    );
                  },
                )}
              </ol>
            </div>
          ))}
        </div>
        <div>
          <div className="mb-2 text-sm font-medium">Registered steps</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {catalog.nodes.map((n) => (
              <div key={n.name} className="flex flex-col gap-0.5 rounded-md border p-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-xs font-semibold">{n.name}</code>
                  <Badge variant="outline">{n.kind}</Badge>
                  {n.plugin && <Badge variant="secondary">plugin</Badge>}
                  {n.routes && <span className="text-muted-foreground text-xs">routes: {n.routes.join(", ")}</span>}
                </div>
                {n.description && <span className="text-muted-foreground text-xs">{n.description}</span>}
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function SettingsPage() {
  const { isAdmin } = useAuth();
  const config = useConfig();
  const workflows = useWorkflows();
  const description = useMemo(
    () =>
      "Configure the LLM and embedding models used by the Auto OMOP Mapper. Changing the embedding model or dimensions uses a new embedding collection, which must be embedded again.",
    [],
  );

  return (
    <div className="space-y-6">
      <PageHeader icon={SettingsIcon} title="Configuration" description={description} />
      <ErrorAlert error={config.error} title="Could not load the configuration" />
      {config.data && <CurrentConfig config={config.data} />}
      {config.data && isAdmin && (
        // Remount (and so reset the form) whenever the saved configuration changes
        <UpdateForm
          key={`${config.data.llm_model}|${config.data.embedding_model}|${config.data.embedding_dims ?? ""}|${config.data.embedding_text}|${config.data.embedding_task_types}`}
          config={config.data}
        />
      )}
      {!isAdmin && (
        <Alert>
          <Info />
          <AlertDescription>Only admins can change the configuration.</AlertDescription>
        </Alert>
      )}
      <ErrorAlert error={workflows.error} title="Could not load pipelines" />
      {workflows.data && <Pipelines catalog={workflows.data} />}
    </div>
  );
}
