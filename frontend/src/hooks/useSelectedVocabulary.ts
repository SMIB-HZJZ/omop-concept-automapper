import { useSourceVocabularies, useUpdateVocabulary } from "@/api/queries";
import type { SourceVocabulary } from "@/api/types";
import { usePersistentState } from "@/hooks/usePersistentState";

/** The selected source vocabulary is shared by the Map, Review and Commit pages. */
export function useSelectedVocabulary() {
  const vocabularies = useSourceVocabularies();
  const update = useUpdateVocabulary();
  const [selectedId, setSelectedId] = usePersistentState<number | null>("vocabulary", null);

  const list = vocabularies.data ?? [];
  // Falls back to the first vocabulary when nothing (or a deleted one) is selected
  const vocabulary: SourceVocabulary | null =
    list.find((v) => v.id === selectedId) ?? (list.length > 0 ? list[0] : null);

  // The vocabulary's own setting is the only switch for drug mode (auto-map and manual mapping)
  const isDrug = vocabulary?.is_drug ?? false;
  const setIsDrug = (value: boolean) => {
    if (vocabulary) update.mutate({ id: vocabulary.id, is_drug: value });
  };

  return {
    vocabularies: list,
    isLoading: vocabularies.isLoading,
    vocabulary,
    vocabularyId: vocabulary?.id ?? null,
    select: setSelectedId,
    isDrug,
    setIsDrug,
    isSavingDrug: update.isPending,
  };
}
