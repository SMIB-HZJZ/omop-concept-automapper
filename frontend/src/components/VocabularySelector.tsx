import { useEffect, useMemo } from "react";
import { Link } from "react-router";

import { useSourceVocabularies, useUpdateVocabulary } from "@/api/queries";
import type { SourceVocabulary } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { usePersistentState } from "@/hooks/usePersistentState";

/** The selected source vocabulary is shared by the Map, Review and Commit pages. */
export function useSelectedVocabulary() {
  const vocabularies = useSourceVocabularies();
  const [selectedId, setSelectedId] = usePersistentState<number | null>("vocabulary", null);
  const [drugOverride, setDrugOverride] = usePersistentState<Record<string, boolean>>("drug-override", {});

  const list = vocabularies.data ?? [];
  const vocabulary: SourceVocabulary | null =
    list.find((v) => v.id === selectedId) ?? (list.length > 0 ? list[0] : null);

  useEffect(() => {
    if (vocabularies.isSuccess && vocabulary && vocabulary.id !== selectedId) setSelectedId(vocabulary.id);
  }, [vocabularies.isSuccess, vocabulary, selectedId, setSelectedId]);

  const isDrug = vocabulary ? (drugOverride[vocabulary.id] ?? vocabulary.is_drug) : false;
  const setIsDrug = (value: boolean) => {
    if (vocabulary) setDrugOverride({ ...drugOverride, [vocabulary.id]: value });
  };

  return {
    vocabularies: list,
    isLoading: vocabularies.isLoading,
    vocabulary,
    vocabularyId: vocabulary?.id ?? null,
    select: setSelectedId,
    isDrug,
    setIsDrug,
    drugDiffersFromDefault: !!vocabulary && isDrug !== vocabulary.is_drug,
  };
}

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
                {v.name} <span className="text-muted-foreground">(id {v.id} · {v.concept_count.toLocaleString()} concepts)</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {showDrug && vocabulary && (
        <div className="flex h-9 items-center gap-2">
          <Checkbox
            id="drug-vocabulary"
            checked={selection.isDrug}
            onCheckedChange={(v) => selection.setIsDrug(!!v)}
          />
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
    </div>
  );
}
