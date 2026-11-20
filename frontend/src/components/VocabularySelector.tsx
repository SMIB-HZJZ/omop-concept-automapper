import { Pill, Stethoscope } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { VocabularyAbout } from "@/components/VocabularyAbout";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { useSelectedVocabulary } from "@/hooks/useSelectedVocabulary";

type Selection = ReturnType<typeof useSelectedVocabulary>;

export function VocabularySelector({ selection }: { selection: Selection }) {
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
      {/* Read-only here: set at import, changed with Edit next to the description (admins) */}
      {vocabulary && (
        <div className="flex h-9 items-center gap-2 text-sm">
          <Badge variant="secondary" className="gap-1">
            {vocabulary.is_drug ? <Pill className="size-3" /> : <Stethoscope className="size-3" />}
            {vocabulary.is_drug ? "Drugs" : "General"}
          </Badge>
          {(vocabulary.code_vocabularies ?? []).length > 0 && (
            <span className="text-muted-foreground" title="Source codes are looked up in these code systems first">
              codes: {(vocabulary.code_vocabularies ?? []).join(", ")}
            </span>
          )}
        </div>
      )}
      {vocabulary && <VocabularyAbout vocabulary={vocabulary} />}
    </div>
  );
}
