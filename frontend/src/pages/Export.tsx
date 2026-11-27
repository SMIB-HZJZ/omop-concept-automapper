import { Download, FileDown, Lock } from "lucide-react";
import { useState } from "react";

import { useSourceVocabularies } from "@/api/queries";
import type { SourceVocabulary } from "@/api/types";
import { ErrorAlert, PageHeader } from "@/components/common";
import { Skeleton } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { combinedExportUrl, defaultLabel, labelProblems, type ExportChoice } from "./exportUrl";

/** One OMOP source_to_concept_map for several published source vocabularies. */
export function ExportPage() {
  const vocabularies = useSourceVocabularies();
  const all = vocabularies.data ?? [];
  const published = all.filter((v) => v.latest_release != null);
  const unpublished = all.filter((v) => v.latest_release == null);
  // Per vocabulary: ticked, release ("" = latest) and label ("" = from the name)
  const [choices, setChoices] = useState<Record<number, Partial<ExportChoice>>>({});
  const update = (id: number, change: Partial<ExportChoice>) =>
    setChoices((c) => ({ ...c, [id]: { ...c[id], ...change } }));

  const selected: ExportChoice[] = published
    .filter((v) => choices[v.id]?.selected)
    .map((v) => ({
      id: v.id,
      selected: true,
      release: choices[v.id]?.release ?? null,
      label: choices[v.id]?.label?.trim() || defaultLabel(v.name, v.id),
    }));
  const problems = labelProblems(selected);
  const allTicked = published.length > 0 && published.every((v) => choices[v.id]?.selected);

  return (
    <div>
      <PageHeader
        icon={FileDown}
        title="Export mappings"
        description="One OMOP source_to_concept_map for several source vocabularies. Only published releases are exported."
      />
      <ErrorAlert error={vocabularies.error} title="Could not load the source vocabularies" />
      <Card>
        <CardHeader>
          <CardTitle>Published vocabularies</CardTitle>
          <CardDescription>
            Each vocabulary&apos;s rows carry its SOURCE_VOCABULARY_ID, so codes from different vocabularies stay apart;
            it must be unique in the file. Earlier, remapped or removed mappings are kept with an expired
            valid_end_date, as in each vocabulary&apos;s own export.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {vocabularies.isPending ? (
            <Skeleton className="h-32 w-full" />
          ) : published.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No source vocabulary is published yet. Publish a release in Verify &amp; publish first.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">
                      <Checkbox
                        aria-label="Select all published vocabularies"
                        checked={allTicked}
                        onCheckedChange={(on) => published.forEach((v) => update(v.id, { selected: on === true }))}
                      />
                    </TableHead>
                    <TableHead>Source vocabulary</TableHead>
                    <TableHead>Release</TableHead>
                    <TableHead>SOURCE_VOCABULARY_ID</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {published.map((v) => (
                    <VocabularyRow
                      key={v.id}
                      vocabulary={v}
                      choice={choices[v.id] ?? {}}
                      onChange={(change) => update(v.id, change)}
                      duplicate={problems.duplicates.has(choices[v.id]?.label?.trim() || defaultLabel(v.name, v.id))}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
          {unpublished.length > 0 && (
            <p className="text-muted-foreground text-xs">
              Not published, so not exported: {unpublished.map((v) => v.name).join(", ")}.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            {selected.length > 0 && !problems.any ? (
              <Button asChild>
                <a href={combinedExportUrl(selected)} download="source_to_concept_map.csv">
                  <Download /> Download source_to_concept_map
                </a>
              </Button>
            ) : (
              <Button disabled>
                <Download /> Download source_to_concept_map
              </Button>
            )}
            <span className="text-muted-foreground text-sm" aria-live="polite">
              {problems.invalid.size > 0
                ? "Use 1–20 letters, digits, _ or - for each SOURCE_VOCABULARY_ID."
                : problems.duplicates.size > 0
                  ? `Used twice: ${[...problems.duplicates].join(", ")}. Give each vocabulary its own ID.`
                  : selected.length === 0
                    ? "Tick the vocabularies to include."
                    : `${selected.length} ${selected.length === 1 ? "vocabulary" : "vocabularies"} in one file`}
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function VocabularyRow({
  vocabulary: v,
  choice,
  onChange,
  duplicate,
}: {
  vocabulary: SourceVocabulary;
  choice: Partial<ExportChoice>;
  onChange: (change: Partial<ExportChoice>) => void;
  duplicate: boolean;
}) {
  const latest = v.latest_release!;
  const releases = Array.from({ length: latest }, (_, i) => latest - i);
  return (
    <TableRow>
      <TableCell>
        <Checkbox
          aria-label={`Include ${v.name}`}
          checked={!!choice.selected}
          onCheckedChange={(on) => onChange({ selected: on === true })}
        />
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2 font-medium">
          {v.name}
          {v.locked && (
            <Badge variant="secondary" className="gap-1" title="Released and locked">
              <Lock className="size-3" /> locked
            </Badge>
          )}
          {!v.locked && (
            <Badge
              variant="outline"
              title={`Unlocked for the next release; changes made since release ${latest} are not exported until it is published`}
            >
              unlocked
            </Badge>
          )}
        </div>
      </TableCell>
      <TableCell>
        <Select
          value={String(choice.release ?? "latest")}
          onValueChange={(value) => onChange({ release: value === "latest" ? null : Number(value) })}
        >
          <SelectTrigger className="w-40" aria-label={`Release of ${v.name}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="latest">Latest ({latest})</SelectItem>
            {releases.slice(1).map((n) => (
              <SelectItem key={n} value={String(n)}>
                Release {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell>
        <Input
          className="w-56 font-mono"
          aria-label={`SOURCE_VOCABULARY_ID of ${v.name}`}
          aria-invalid={duplicate || undefined}
          maxLength={20}
          placeholder={defaultLabel(v.name, v.id)}
          value={choice.label ?? ""}
          onChange={(e) => onChange({ label: e.target.value })}
        />
      </TableCell>
    </TableRow>
  );
}
