import { useMemo } from "react";
import { Link } from "react-router";

import { useUpdateVocabulary } from "@/api/queries";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { MultiSelect } from "@/components/MultiSelect";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { useSelectedVocabulary } from "@/hooks/useSelectedVocabulary";

type Selection = ReturnType<typeof useSelectedVocabulary>;

export function VocabularySelector({ selection, showDrug = true }: { selection: Selection; showDrug?: boolean }) {
  const update = useUpdateVocabulary();
  const { vocabularies, vocabulary, isLoading } = selection;
  const sorted = useMemo(() => [...vocabularies].sort((a, b) => a.id - b.id), [vocabularies]);

  if (!isLoading && vocabularies.length === 0) {
    return (
      <Alert>
        <AlertDescription>
          No source vocabularies found. Please{" "}
          <Link to="/import?tab=source" className="underline">
            import source concepts
          </Link>{" "}
          first.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="grid gap-1.5">
        <Label htmlFor="vocabulary-select">Source vocabulary</Label>
        <Select
          value={vocabulary ? String(vocabulary.id) : undefined}
          onValueChange={(v) => selection.select(Number(v))}
        >
          <SelectTrigger id="vocabulary-select" className="w-72">
            <SelectValue placeholder={isLoading ? "Loading…" : "Select a vocabulary"} />
          </SelectTrigger>
          <SelectContent>
            {sorted.map((v) => (
              <SelectItem key={v.id} value={String(v.id)}>
                {v.name}{" "}
                <span className="text-muted-foreground">
                  (id {v.id} · {v.concept_count.toLocaleString()} concepts)
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {showDrug && vocabulary && (
        <div className="flex h-9 items-center gap-2">
          <Checkbox id="drug-vocabulary" checked={selection.isDrug} onCheckedChange={(v) => selection.setIsDrug(!!v)} />
          <Label htmlFor="drug-vocabulary">Drug vocabulary</Label>
          {selection.drugDiffersFromDefault && (
            <Button
              variant="link"
              size="sm"
              className="h-auto px-1"
              disabled={update.isPending}
              onClick={() => update.mutate({ id: vocabulary.id, is_drug: selection.isDrug })}
            >
              save as default
            </Button>
          )}
        </div>
      )}
      {vocabulary && (
        <div className="grid gap-1.5">
          <Label htmlFor="code-vocabularies">Source codes are</Label>
          <MultiSelect
            id="code-vocabularies"
            className="w-56"
            options={CODE_VOCABULARIES}
            value={vocabulary.code_vocabularies ?? []}
            onChange={(code_vocabularies) => update.mutate({ id: vocabulary.id, code_vocabularies })}
            placeholder="no code mapping"
          />
        </div>
      )}
    </div>
  );
}

/** OMOP vocabularies whose codes can appear in source_value (MKB-10 = ICD10). Codes found in
 * these are mapped deterministically through "Maps to". */
const CODE_VOCABULARIES = ["ICD10", "ICD10CM", "ICD9CM", "ICD10PCS", "LOINC", "SNOMED", "ATC", "NDC", "CPT4", "Read"];
