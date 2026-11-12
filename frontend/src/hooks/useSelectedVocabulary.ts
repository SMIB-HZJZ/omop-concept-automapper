import { useSourceVocabularies } from "@/api/queries";
import type { SourceVocabulary } from "@/api/types";
import { usePersistentState } from "@/hooks/usePersistentState";

/** The selected source vocabulary is shared by the Map, Review and Commit pages. */
export function useSelectedVocabulary() {
  const vocabularies = useSourceVocabularies();
  const [selectedId, setSelectedId] = usePersistentState<number | null>("vocabulary", null);
  const [drugOverride, setDrugOverride] = usePersistentState<Record<string, boolean>>("drug-override", {});

  const list = vocabularies.data ?? [];
  // Falls back to the first vocabulary when nothing (or a deleted one) is selected
  const vocabulary: SourceVocabulary | null =
    list.find((v) => v.id === selectedId) ?? (list.length > 0 ? list[0] : null);

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
