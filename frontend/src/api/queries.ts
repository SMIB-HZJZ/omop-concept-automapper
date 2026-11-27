import {
  keepPreviousData,
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import { api, ApiError, unwrap } from "./client";
import type { ConfigUpdate, DrugStructure, MatchType, SearchRequest, SuggestRequest } from "./types";

/** Query keys. Everything about one source vocabulary lives under ["vocab", id]. */
export const keys = {
  me: ["me"] as const,
  meta: ["meta"] as const,
  config: ["config"] as const,
  models: ["models"] as const,
  collectionPreview: (model: string, dims: number | null) => ["collection-preview", model, dims] as const,
  vocabularyStatus: ["vocabulary-status"] as const,
  atc7Stats: ["atc7-stats"] as const,
  sourceVocabularies: ["source-vocabularies"] as const,
  vocab: (id: number) => ["vocab", id] as const,
  unmapped: (id: number, page: number, perPage: number, view: UnmappedView, q: string) =>
    ["vocab", id, "unmapped", page, perPage, view, q] as const,
  mapped: (id: number, page: number, perPage: number, q: string, flagged: boolean) =>
    ["vocab", id, "mapped", page, perPage, q, flagged] as const,
  stats: (id: number) => ["vocab", id, "stats"] as const,
  releases: (id: number) => ["vocab", id, "releases"] as const,
  suggestions: (id: number, params: object) => ["vocab", id, "suggestions", params] as const,
  embeddingStatus: (domains: string[]) => ["embedding-status", [...domains].sort()] as const,
  tasks: (params: object) => ["tasks", params] as const,
  task: (id: string) => ["task", id] as const,
  workflows: ["workflows"] as const,
  users: ["users"] as const,
  search: (body: object) => ["search", body] as const,
  drugMatch: (sourceId: number, structure: object | null) => ["drug-match", sourceId, structure] as const,
  translation: (sourceId: number) => ["translation", sourceId] as const,
  concepts: (ids: string) => ["concepts", ids] as const,
  conceptDetails: (id: number) => ["concept-details", id] as const,
  presence: (vocabularyId: number) => ["presence", vocabularyId] as const,
  activity: (vocabularyId: number) => ["activity", vocabularyId] as const,
  conceptExplanation: (id: number) => ["concept-explanation", id] as const,
};

export function invalidateVocabulary(qc: QueryClient, id?: number) {
  void qc.invalidateQueries({ queryKey: keys.sourceVocabularies });
  void qc.invalidateQueries({ queryKey: id === undefined ? ["vocab"] : keys.vocab(id) });
}

/** A mapping write refused with 409 (locked by a release, or mapped meanwhile): the screen is
 * out of date, so its lock state and lists are fetched again. */
function refreshOnConflict(qc: QueryClient, vocabularyId: number | null) {
  return (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) invalidateVocabulary(qc, vocabularyId ?? undefined);
  };
}

// ----------------------------------------------------------------------------- meta / config

export const useMeta = () =>
  useQuery({ queryKey: keys.meta, queryFn: async () => unwrap(await api.GET("/api/meta")), staleTime: 5 * 60_000 });

/** Domain / vocabulary / concept class combinations of standard concepts (changes only with a
 *  vocabulary import), for the Athena-style search filters. */
export const useConceptFacets = () =>
  useQuery({
    queryKey: ["concept-facets"],
    queryFn: async () => unwrap(await api.GET("/api/concept-facets")),
    staleTime: 30 * 60_000,
  });

export const useConfig = () =>
  useQuery({ queryKey: keys.config, queryFn: async () => unwrap(await api.GET("/api/config")) });

export const useModelOptions = () =>
  useQuery({
    queryKey: keys.models,
    queryFn: async () => unwrap(await api.GET("/api/config/models")),
    staleTime: 60 * 60_000,
  });

export const useCollectionPreview = (
  model: string,
  dims: number | null,
  text: string,
  taskTypes: boolean,
  enabled: boolean,
) =>
  useQuery({
    queryKey: [...keys.collectionPreview(model, dims), text, taskTypes],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/config/collection-preview", {
          params: { query: { model, dims: dims ?? undefined, text, task_types: taskTypes } },
        }),
      ),
    enabled,
  });

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ConfigUpdate) => unwrap(await api.PUT("/api/config", { body })),
    onSuccess: (data) => {
      qc.setQueryData(keys.config, data);
      void qc.invalidateQueries({ queryKey: ["embedding-status"] });
    },
  });
}

export function useResetConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/config/reset")),
    onSuccess: (data) => {
      qc.setQueryData(keys.config, data);
      void qc.invalidateQueries({ queryKey: ["embedding-status"] });
    },
  });
}

export const useWorkflows = () =>
  useQuery({ queryKey: keys.workflows, queryFn: async () => unwrap(await api.GET("/api/workflows")) });

// ----------------------------------------------------------------------------- vocabulary / atc7 / embeddings

export const useVocabularyStatus = () =>
  useQuery({ queryKey: keys.vocabularyStatus, queryFn: async () => unwrap(await api.GET("/api/vocabulary/status")) });

export const useAtc7Stats = () =>
  useQuery({ queryKey: keys.atc7Stats, queryFn: async () => unwrap(await api.GET("/api/atc7/stats")) });

export const useEmbeddingStatus = (domains: string[]) =>
  useQuery({
    queryKey: keys.embeddingStatus(domains),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/embeddings/status", {
          params: { query: { domain: domains.length ? domains : undefined } },
        }),
      ),
  });

// ----------------------------------------------------------------------------- source vocabularies / mapping

export const useSourceVocabularies = () =>
  useQuery({
    queryKey: keys.sourceVocabularies,
    queryFn: async () => unwrap(await api.GET("/api/source-vocabularies")),
    // Lock state and counts change through other people (a release, a colleague's mappings)
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

/** todo: not flagged; suggested: with a waiting AI suggestion; flagged; all */
export type UnmappedView = "todo" | "suggested" | "flagged" | "all";

export const useUnmapped = (id: number | null, page: number, perPage: number, view: UnmappedView = "todo", q = "") =>
  useQuery({
    queryKey: keys.unmapped(id ?? 0, page, perPage, view, q),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/source-vocabularies/{vocabulary_id}/unmapped", {
          params: { path: { vocabulary_id: id! }, query: { page, per_page: perPage, view, q: q || undefined } },
        }),
      ),
    enabled: id !== null,
    placeholderData: keepPreviousData,
    // Colleagues map terms too: refresh while the page is open (not in a background tab)
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

export const useMapped = (id: number | null, page: number, perPage: number, q: string, flagged = false) =>
  useQuery({
    queryKey: keys.mapped(id ?? 0, page, perPage, q, flagged),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/source-vocabularies/{vocabulary_id}/mapped", {
          params: {
            path: { vocabulary_id: id! },
            query: { page, per_page: perPage, q: q || undefined, flagged: flagged || undefined },
          },
        }),
      ),
    enabled: id !== null,
    placeholderData: keepPreviousData,
  });

export const useMappingStats = (id: number | null) =>
  useQuery({
    queryKey: keys.stats(id ?? 0),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/source-vocabularies/{vocabulary_id}/stats", { params: { path: { vocabulary_id: id! } } }),
      ),
    enabled: id !== null,
  });

export type SuggestionFilters = {
  status: "pending" | "accepted" | "rejected" | "below_threshold";
  source_id?: number;
  min_confidence?: number;
  page: number;
  per_page: number;
  sort: string;
};

export const useSuggestions = (id: number | null, filters: SuggestionFilters) =>
  useQuery({
    queryKey: keys.suggestions(id ?? 0, filters),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/source-vocabularies/{vocabulary_id}/suggestions", {
          params: { path: { vocabulary_id: id! }, query: filters },
        }),
      ),
    enabled: id !== null,
    placeholderData: keepPreviousData,
  });

export function useMapConcepts(vocabularyId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { source_id: number; concept_ids: number[]; match_type: MatchType; replace: boolean }) =>
      unwrap(await api.POST("/api/mappings", { body })),
    onSuccess: () => invalidateVocabulary(qc, vocabularyId ?? undefined),
    onError: refreshOnConflict(qc, vocabularyId),
  });
}

export function useUnmap(vocabularyId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (source_ids: number[]) => unwrap(await api.POST("/api/mappings/unmap", { body: { source_ids } })),
    onSuccess: () => invalidateVocabulary(qc, vocabularyId ?? undefined),
    onError: refreshOnConflict(qc, vocabularyId),
  });
}

export function useUpdateVocabulary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      ...body
    }: {
      id: number;
      name?: string;
      description?: string;
      is_drug?: boolean;
      code_vocabularies?: string[];
      source_language?: string | null;
    }) =>
      unwrap(
        await api.PATCH("/api/source-vocabularies/{vocabulary_id}", { params: { path: { vocabulary_id: id } }, body }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.sourceVocabularies }),
  });
}

// ----------------------------------------------------------------------------- releases

export const useReleases = (vocabularyId: number | null) =>
  useQuery({
    queryKey: keys.releases(vocabularyId ?? 0),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/source-vocabularies/{vocabulary_id}/releases", {
          params: { path: { vocabulary_id: vocabularyId! } },
        }),
      ),
    enabled: vocabularyId !== null,
  });

/** Publish the current mappings as the next release (locks the vocabulary). */
export function usePublishRelease(vocabularyId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (note: string) =>
      unwrap(
        await api.POST("/api/source-vocabularies/{vocabulary_id}/releases", {
          params: { path: { vocabulary_id: vocabularyId! } },
          body: { note: note.trim() || null },
        }),
      ),
    onSuccess: () => invalidateVocabulary(qc, vocabularyId ?? undefined),
  });
}

/** Unlock a released vocabulary: changes from now on go into the next release. */
export function useStartNextRelease(vocabularyId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/source-vocabularies/{vocabulary_id}/releases/next", {
          params: { path: { vocabulary_id: vocabularyId! } },
        }),
      ),
    onSuccess: () => invalidateVocabulary(qc, vocabularyId ?? undefined),
  });
}

export function useDeleteVocabulary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await api.DELETE("/api/source-vocabularies/{vocabulary_id}", { params: { path: { vocabulary_id: id } } })),
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: keys.vocab(id) });
      void qc.invalidateQueries({ queryKey: keys.sourceVocabularies });
    },
  });
}

export function useDecideSuggestions(vocabularyId: number | null, decision: "accept" | "reject") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { ids?: number[]; vocabulary_id?: number; min_confidence?: number }) =>
      unwrap(
        decision === "accept"
          ? await api.POST("/api/suggestions/accept", { body })
          : await api.POST("/api/suggestions/reject", { body }),
      ),
    onSuccess: () => {
      invalidateVocabulary(qc, vocabularyId ?? undefined);
      void qc.invalidateQueries({ queryKey: ["calibration"] });
    },
    onError: refreshOnConflict(qc, vocabularyId),
  });
}

export function useSuggest() {
  return useMutation({
    mutationFn: async ({ sourceId, progress, ...body }: SuggestRequest & { sourceId: number; progress?: string }) =>
      unwrap(
        await api.POST("/api/source-concepts/{source_id}/suggest", {
          params: { path: { source_id: sourceId }, query: progress ? { progress } : {} },
          body,
        }),
      ),
  });
}

/** The step a running AI suggest is at (polled while it runs; null before its first step). */
export const useSuggestProgress = (progressId: string | null) =>
  useQuery({
    queryKey: ["suggest-progress", progressId],
    queryFn: async () =>
      unwrap(await api.GET("/api/suggest-progress/{progress_id}", { params: { path: { progress_id: progressId! } } })),
    enabled: progressId !== null,
    refetchInterval: 500,
    gcTime: 0,
  });

// ----------------------------------------------------------------------------- search / concepts

/** Auto-mapping's deterministic drug step for one term: its structure (from the CSV, edited, or
 * read from the name by the AI) matched on drug_strength, with the matching drugs. */
export const drugMatchQuery = (sourceId: number, structure: DrugStructure | null) =>
  queryOptions({
    queryKey: keys.drugMatch(sourceId, structure),
    queryFn: async () =>
      unwrap(
        await api.POST("/api/source-concepts/{source_id}/drug-match", {
          params: { path: { source_id: sourceId } },
          body: { structure },
        }),
      ),
    // The match only changes with the vocabulary; parsing a name costs an AI call
    staleTime: 10 * 60_000,
    retry: false,
  });

/** The term in English, as auto-mapping's translate step renders it (cached on the server, so a
 * term auto-mapping has seen costs no AI call). Searched next to the source term. */
export const translationQuery = (sourceId: number) =>
  queryOptions({
    queryKey: keys.translation(sourceId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/source-concepts/{source_id}/translation", {
          params: { path: { source_id: sourceId } },
        }),
      ),
    // A failed translation (an LLM outage) is retried on the next visit, not kept for 30 minutes
    staleTime: (q) => (q.state.data?.failed ? 0 : 30 * 60_000),
    retry: false,
  });

export const useTermTranslation = (sourceId: number, enabled: boolean) =>
  useQuery({ ...translationQuery(sourceId), enabled });

/** Free text in English (the Search page), with auto-mapping's translator; cached on the server. */
export const useTextTranslation = (text: string, enabled: boolean) =>
  useQuery({
    queryKey: ["translate-text", text],
    queryFn: async () => unwrap(await api.POST("/api/translate", { body: { text } })),
    enabled: enabled && text.trim().length > 0,
    // A failed translation (an LLM outage) is retried on the next visit, not kept for 30 minutes
    staleTime: (q) => (q.state.data?.failed ? 0 : 30 * 60_000),
    retry: false,
  });

export const useDrugMatch = (sourceId: number, structure: DrugStructure | null, enabled: boolean) =>
  useQuery({ ...drugMatchQuery(sourceId, structure), enabled });

/** One search; also used to load the next term's candidates early (same key, same data). */
export const searchQuery = (body: SearchRequest) =>
  queryOptions({
    queryKey: keys.search(body),
    // Results carry their request: with keepPreviousData the shown results may belong to an older one
    queryFn: async () => ({ ...unwrap(await api.POST("/api/search", { body })), request: body }),
    staleTime: 60_000,
    retry: false,
  });

export const useSearch = (body: SearchRequest | null) =>
  useQuery({
    ...searchQuery(body ?? ({ query: "" } as SearchRequest)),
    enabled: body !== null && body.query.trim().length > 0,
    placeholderData: keepPreviousData,
  });

export const useConceptLookup = (ids: string) =>
  useQuery({
    queryKey: keys.concepts(ids),
    queryFn: async () => unwrap(await api.GET("/api/concepts", { params: { query: { ids } } })),
    enabled: ids.trim().length > 0,
    retry: false,
  });

// ----------------------------------------------------------------------------- users

export const useUsers = (enabled = true) =>
  useQuery({ queryKey: keys.users, queryFn: async () => unwrap(await api.GET("/api/users")), enabled });

/** What the sign-in page offers (public: works signed out). */
export const useAuthOptions = () =>
  useQuery({
    queryKey: ["auth-options"],
    queryFn: async () => unwrap(await api.GET("/api/auth/options")),
    staleTime: 5 * 60_000,
    retry: false,
  });

// ----------------------------------------------------------------------------- concept details

export const useConceptDetails = (conceptId: number | null) =>
  useQuery({
    queryKey: keys.conceptDetails(conceptId ?? 0),
    queryFn: async () =>
      unwrap(await api.GET("/api/concepts/{concept_id}/details", { params: { path: { concept_id: conceptId! } } })),
    enabled: conceptId !== null,
    // Vocabulary data changes only with a vocabulary import
    staleTime: 30 * 60_000,
  });

/** The AI explanation is generated on request (it costs an LLM call) and kept for the session. */
export const useConceptExplanation = (conceptId: number, requested: boolean) =>
  useQuery({
    queryKey: keys.conceptExplanation(conceptId),
    queryFn: async () =>
      unwrap(await api.POST("/api/concepts/{concept_id}/explain", { params: { path: { concept_id: conceptId } } })),
    enabled: requested,
    staleTime: Infinity,
    retry: false,
  });

// ----------------------------------------------------------------------------- flags

export function useFlagSource(vocabularyId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      sourceId,
      flag,
      note,
    }: {
      sourceId: number;
      flag: "unmappable" | "question" | null;
      note?: string | null;
    }) =>
      unwrap(
        await api.PUT("/api/source-concepts/{source_id}/flag", {
          params: { path: { source_id: sourceId } },
          body: { flag, note: note ?? null },
        }),
      ),
    onSuccess: () => invalidateVocabulary(qc, vocabularyId ?? undefined),
  });
}

// ----------------------------------------------------------------------------- collaboration

/** Who has which term open in this vocabulary. While ``sourceId`` is set, this user's presence on
 * that term is renewed with every poll (the server forgets it after 45 s without renewal). */
export const usePresence = (vocabularyId: number | null, sourceId: number | null) =>
  useQuery({
    queryKey: [...keys.presence(vocabularyId ?? 0), sourceId] as const,
    queryFn: async () =>
      sourceId !== null
        ? unwrap(
            await api.PUT("/api/source-concepts/{source_id}/presence", {
              params: { path: { source_id: sourceId } },
            }),
          )
        : unwrap(
            await api.GET("/api/source-vocabularies/{vocabulary_id}/presence", {
              params: { path: { vocabulary_id: vocabularyId! } },
            }),
          ),
    enabled: vocabularyId !== null,
    refetchInterval: 20_000,
    refetchIntervalInBackground: false,
    staleTime: 0,
  });

export const useTeamActivity = (vocabularyId: number | null, enabled = true) =>
  useQuery({
    queryKey: keys.activity(vocabularyId ?? 0),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/source-vocabularies/{vocabulary_id}/activity", {
          params: { path: { vocabulary_id: vocabularyId! } },
        }),
      ),
    enabled: vocabularyId !== null && enabled,
    refetchInterval: 60_000,
  });

export async function leaveVocabulary(vocabularyId: number) {
  await api.DELETE("/api/source-vocabularies/{vocabulary_id}/presence", {
    params: { path: { vocabulary_id: vocabularyId } },
  });
}
