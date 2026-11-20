import { useMemo } from "react";
import { Link } from "react-router";

import { useUpdateVocabulary } from "@/api/queries";
import { useAuth } from "@/auth/useAuth";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { MultiSelect } from "@/components/MultiSelect";
import { VocabularyAbout } from "@/components/VocabularyAbout";
import { VocabularyTypeSwitch } from "@/components/VocabularyTypeSwitch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { useSelectedVocabulary } from "@/hooks/useSelectedVocabulary";

type Selection = ReturnType<typeof useSelectedVocabulary>;

export function VocabularySelector({ selection, showDrug = true }: { selection: Selection; showDrug?: boolean }) {
  const update = useUpdateVocabulary();
  const { isAdmin } = useAuth();
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
          <SelectTrigger id="vocabulary-select" className="w-80 max-w-full">
            <SelectValue placeholder={isLoading ? "Loading…" : "Select a vocabulary"} />
          </SelectTrigger>
          <SelectContent>
            {sorted.map((v) => (
              <SelectItem key={v.id} value={String(v.id)}>
                {v.name} <span className="text-muted-foreground">· {v.concept_count.toLocaleString()} terms</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {/* Vocabulary settings are the admin's; users see them read-only */}
      {showDrug && vocabulary && isAdmin && (
        <VocabularyTypeSwitch
          isDrug={selection.isDrug}
          onChange={selection.setIsDrug}
          disabled={selection.isSavingDrug}
        />
      )}
      {vocabulary && !isAdmin && (
        <div className="flex h-9 items-center gap-2 text-sm">
          <Badge variant="secondary">{vocabulary.is_drug ? "Drugs" : "General"}</Badge>
          {(vocabulary.code_vocabularies ?? []).length > 0 && (
            <span className="text-muted-foreground">codes: {(vocabulary.code_vocabularies ?? []).join(", ")}</span>
          )}
        </div>
      )}
      {vocabulary && isAdmin && (
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
      {vocabulary && <VocabularyAbout vocabulary={vocabulary} />}
    </div>
  );
}

/** OMOP vocabularies whose codes can appear in source_value (MKB-10 = ICD10). Codes found in
 * these are mapped deterministically through "Maps to". */
const CODE_VOCABULARIES = ["ICD10", "ICD10CM", "ICD9CM", "ICD10PCS", "LOINC", "SNOMED", "ATC", "NDC", "CPT4", "Read"];
