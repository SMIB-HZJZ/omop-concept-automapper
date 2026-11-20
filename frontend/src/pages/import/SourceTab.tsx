import { Info, Plus } from "lucide-react";
import Papa from "papaparse";
import { useState } from "react";
import { Link } from "react-router";

import { api, unwrap } from "@/api/client";
import { useSourceVocabularies } from "@/api/queries";
import { taskResult } from "@/api/taskResults";
import type { Task } from "@/api/types";
import { ErrorAlert } from "@/components/common";
import { TaskProgress } from "@/components/TaskProgress";
import { VocabularyDescriptionField } from "@/components/VocabularyDescriptionField";
import { VocabularyTypeSwitch } from "@/components/VocabularyTypeSwitch";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useStartTask } from "@/hooks/useStartTask";
import { useLatestTask } from "@/hooks/useTasks";
import { missingSourceColumns } from "@/lib/csv";
import { formatNumber } from "@/lib/format";
import { VOCABULARY_NAME_MAX } from "@/lib/vocabularyText";

type Preview = { columns: string[]; rows: Record<string, string>[] };

function SourceImportResult({ task }: { task: Task }) {
  const r = taskResult(task, "source.import");
  if (!r) return null;
  const invalid = r.invalid_concept_ids;
  return (
    <div className="space-y-1">
      <div>
        Imported {formatNumber(r.inserted)} source concepts into vocabulary {r.vocabulary_id}
        {r.filename ? ` from ${r.filename}` : ""}.
      </div>
      <div>
        {formatNumber(r.premapped_sources)} concepts came with existing mappings ({formatNumber(r.premapped_links)}{" "}
        links).
      </div>
      {(r.with_drug_structure ?? 0) > 0 && (
        <div>{formatNumber(r.with_drug_structure ?? 0)} concepts came with a structured drug form.</div>
      )}
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
}

function PreviewTable({ preview }: { preview: Preview }) {
  return (
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
  );
}

export function SourceTab() {
  const vocabularies = useSourceVocabularies();
  const latest = useLatestTask("source.import");
  const { run, busy } = useStartTask();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  // "new" creates a vocabulary (the next free id); a number adds concepts to that vocabulary
  const [target, setTarget] = useState<"new" | number>("new");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isDrug, setIsDrug] = useState(false);

  const existing = target === "new" ? undefined : vocabularies.data?.find((v) => v.id === target);
  // Appending to an existing vocabulary starts from its saved name, description and type
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const existingId = existing?.id ?? null;
  if (existingId !== loadedFor) {
    setLoadedFor(existingId);
    setName(existing?.name ?? "");
    setDescription(existing?.description ?? "");
    setIsDrug(existing?.is_drug ?? false);
  }
  const nameMissing = !name.trim();
  const missing = preview ? missingSourceColumns(preview.columns) : [];
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

  const submit = (upload: File) => {
    const fields = { name: name.trim(), description: description.trim(), is_drug: isDrug };
    // The schema types the file part as a string; the serializer sends the real File
    const bodySerializer = (body: { name?: string | null; description?: string | null; is_drug?: boolean | null }) => {
      const fd = new FormData();
      fd.append("file", upload);
      if (body.name) fd.append("name", body.name);
      fd.append("description", body.description ?? "");
      fd.append("is_drug", body.is_drug ? "true" : "false");
      return fd;
    };
    return run(
      async () =>
        existing
          ? unwrap(
              await api.POST("/api/source-vocabularies/{vocabulary_id}/concepts/import", {
                params: { path: { vocabulary_id: existing.id } },
                body: { file: upload.name, ...fields },
                bodySerializer,
              }),
            )
          : unwrap(
              await api.POST("/api/source-vocabularies/import", {
                body: { file: upload.name, ...fields },
                bodySerializer,
              }),
            ),
      existing ? `Add concepts to ${existing.name}` : "Source concept import",
    );
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Import source concepts</CardTitle>
          <CardDescription>
            <div className="space-y-2">
              <p>
                Upload a CSV file containing your source concepts that need to be mapped to OMOP. The file should be
                comma delimited, not tab (like OMOP vocabularies).
              </p>
              <p>
                Required columns: <code>source_value</code> (source concept code/value),{" "}
                <code>source_concept_name</code> (name or description of the source concept).
              </p>
              <p>
                Optional columns: <code>freq</code> (frequency/count of usage; empty means 1), <code>concept_id</code>{" "}
                (OMOP concept ID(s) for existing mappings; separate multiple IDs with semicolons, e.g.
                "1234;5678;9012").
              </p>
              <p>
                Optional drug columns (shape of OMOP <code>drug_strength</code>, one value per ingredient separated by
                semicolons): <code>ingredient</code>, <code>amount_value</code>, <code>amount_unit</code>,{" "}
                <code>numerator_value</code>, <code>numerator_unit</code>, <code>denominator_value</code>,{" "}
                <code>denominator_unit</code>, <code>dose_form</code>, <code>brand_name</code>. When present, drugs are
                matched deterministically from these instead of parsing the name, e.g. ingredient
                "amoxicillin;clavulanate", amount_value "875;125", amount_unit "mg", dose_form "Oral Tablet".
              </p>
            </div>
          </CardDescription>
        </CardHeader>
      </Card>

      <Alert>
        <Info />
        <AlertTitle>Drug vocabularies</AlertTitle>
        <AlertDescription>
          <p>
            Set <b>Vocabulary type</b> to <b>Drugs</b> for medicine lists. Mapping then uses drug rules: an ATC7 code at
            the start of <code>source_value</code> (e.g. 'A10BA02XYZ') narrows the search, and drugs are matched on
            ingredient, strength and dose form. You can change the type later on the Map page.
          </p>
        </AlertDescription>
      </Alert>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor="source-file">Source concepts CSV</Label>
          <Input
            id="source-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => onFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="source-vocab-target">Import into</Label>
          <Select value={String(target)} onValueChange={(v) => setTarget(v === "new" ? "new" : Number(v))}>
            <SelectTrigger id="source-vocab-target" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="new">
                <Plus className="size-4" /> New vocabulary
              </SelectItem>
              {(vocabularies.data ?? []).map((v) => (
                <SelectItem key={v.id} value={String(v.id)}>
                  {v.name} <span className="text-muted-foreground">· {formatNumber(v.concept_count)} terms</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-muted-foreground text-xs">
            {existing
              ? `The file's concepts are added to "${existing.name}" (ID ${existing.id}); its name and description below are updated.`
              : "Creates a new source vocabulary; it gets the next free ID."}
          </p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="source-vocab-name">Name</Label>
          <Input
            id="source-vocab-name"
            value={name}
            required
            maxLength={VOCABULARY_NAME_MAX}
            aria-invalid={!!file && nameMissing}
            placeholder="e.g. Lab orders, primary care"
            onChange={(e) => setName(e.target.value)}
          />
          {!!file && nameMissing && <p className="text-destructive text-xs">Give the vocabulary a name.</p>}
        </div>
        <VocabularyTypeSwitch isDrug={isDrug} onChange={setIsDrug} />
        <VocabularyDescriptionField
          id="source-vocab-description"
          className="md:col-span-2"
          value={description}
          onChange={setDescription}
        />
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
          {showPreview && <PreviewTable preview={preview} />}
        </div>
      )}

      <Button
        variant="cta"
        disabled={!file || !preview || missing.length > 0 || nameMissing || busy || !!active}
        onClick={() => file && void submit(file)}
      >
        Import source concepts
      </Button>

      {latest.data && <TaskProgress task={latest.data} renderResult={(t) => <SourceImportResult task={t} />} />}
    </div>
  );
}
