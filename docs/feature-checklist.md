# Feature checklist (Step 1 inventory → rewrite)

Verified on 2026-10-07 against the full `docker compose up` stack (PostgreSQL 18 + pgvectorscale, Redis, Celery worker, nginx frontend, LiteLLM). LLM calls went through a fake OpenAI-compatible gateway (`backend/tests/fake_llm`), because no provider key was available.

**Evidence**
- **pytest:** `docker compose -f docker-compose.dev.yaml exec backend pytest`, 78 tests.
- **API walk-through:** curl calls through nginx covering every endpoint group: import → ATC7 → CSV → embed → search → auto-map → review → export → AI suggest → sync.
- **UI smoke test:** `frontend/e2e/smoke.mjs`, 18/18 steps, with no page errors, no console errors and no 5xx responses.

## Features

| ID | Feature | Where now | Verified by |
|---|---|---|---|
| F1 | Vocabulary row counts + last import | Import › Vocabulary · `GET /vocabulary/status` | test_vocabulary_import_and_atc7, UI |
| F2 | Vocabulary zip upload (checked: zip, tables present, size limit) | Import › Vocabulary · `POST /vocabulary/import` (zip as the request body) | test_vocabulary_upload_is_checked, test_vocabulary_upload_size_limit, UI |
| F3 | Import OMOP vocabulary (upsert concept, truncate+copy others, index drop/recreate, `vocabulary_imports` log, clean non-standard mappings) | Celery `vocabulary.import_all` (COPY FROM STDIN, byte progress) | test_vocabulary_import_and_atc7, test_reimport_upserts_concepts, test_clean_non_standard_mappings_after_reimport, e2e |
| F4 | Process ATC7 | Part of the vocabulary import (no separate step) | test_vocabulary_import_and_atc7, e2e |
| F5 | ATC7 statistics | Import › ATC7 · `GET /atc7/stats` | test, UI |
| F6 | Source CSV upload, preview, vocabulary id, freq NaN→1, `;` pre-mappings validated | Import › Source (papaparse preview/validation) · Celery `source.import_csv` | test_source_import_and_mapping_flow, test_source_import_validates_columns, e2e |
| F7 | Embedding status, domain filter, batch size, embed standard concepts | Import › Embeddings · Celery `embeddings.embed_standard` | test_embed_status_and_index, e2e, UI |
| F8 | Sync standard metadata | `embeddings.sync_metadata` (prune non-standard + relabel; metadata is joined live) | test_sync_tasks, e2e |
| F9 | Sync ATC7 only | `embeddings.sync_atc7` (coverage report; ATC7 is joined live) | test_sync_tasks |
| F10 | Clear all embeddings | `DELETE /embeddings` (confirm by typing the collection name) | test_clear_embeddings_in_other_collection |
| F11 | Search (query, domains, k, vocabulary, ATC7; rank/score/details/≤5 ATC7) | Search page · `POST /search` | test_search_filters, UI |
| F12 | Source vocabulary selector + drug checkbox | `VocabularySelector` (shared across pages; drug default stored per vocabulary) | UI |
| F13 | Mapping progress metrics + bar, all-mapped state | Map · `GET …/stats` | test, UI (desktop and mobile) |
| F14 | Audit trail: per-method stats + last 10 | Map › audit trail | test (audit stats/recent), UI |
| F15 | Pagination 25/50/100, page X of Y | Map | test (unmapped paging), UI |
| F16 | Auto-map all (domains, threshold, drug toggle) | Map › Auto map settings · Celery `automap.run` (review/commit modes, concurrency, cancel) | test_automap_review_mode…, test_automap_commit_mode, test_cooperative_cancel, e2e |
| F17 | Unmapped table, freq-ordered, single select | Map | UI |
| F18 | Concept detail: editable name, filters (domains kept, vocabulary, ATC7 auto/manual + validation, max results), auto-search, search method | Map › ConceptDetail (400 ms debounce) | UI |
| F19 | Results multi-select + manual concept IDs with "not found" | Map · `GET /concepts?ids=` | test_concept_lookup, UI |
| F20 | Selected-concepts panel + confirm (replace mappings, `manual` audit row) | Map · `POST /mappings` | test_source_import_and_mapping_flow, UI |
| F21 | Mapped table + unmap selected | Commit · `POST /mappings/unmap` | test, UI |
| F22 | Download `vocabulary_{id}_mappings.csv` | Commit · `GET …/mapped.csv` (same columns as before) | test, e2e |
| F23 | Metrics + mappings-by-domain chart | Commit | test (stats), UI |
| F24 | Delete source vocabulary | Commit (admin; typed confirmation) | test_delete_vocabulary |
| F25 | Current configuration display | Configuration | test_effective_config_defaults_to_env, UI |
| F26 | Change LLM/embedding model/dims, collection preview, confirm, save, reset | Configuration · `PUT /config`, `POST /config/reset`, `GET /config/collection-preview` | test_config.py, UI |
| F27 | Model lists from provider with fallback, cached | `GET /config/models` (env options + gateway `/v1/models`, Redis 1 h) | test_collection_preview_and_models |
| F28 | Wait for DB, migrate schema, log stats on start | `python -m app.cli init` (Alembic, stamp-on-existing) | container logs; upgrade test |
| F29 | Default configuration | env defaults + overrides (`app_config`) | test_config.py |
| F30 | Structured logging | JSON logs (`LOG_FORMAT`) | container logs |
| F31 | Collection naming from model + dims | `collection_name()` | test_effective_config_defaults_to_env |

## Approved additions

| ID | Feature | Verified by |
|---|---|---|
| I1 | `source_vocabulary` table (name, description, is_drug) | test_vocabulary_crud, migration on restored PG15 data |
| I2 | Review queue (accept/reject by selection or ≥ N) | test_automap_review_mode_then_accept_and_reject, e2e, UI |
| I3 | Per-concept AI suggest with trace | test_ai_suggest_single_concept, e2e, UI |
| I4 | Concurrent, cancellable auto-mapping | test_cooperative_cancel |
| — | Auth with user/admin roles | test_auth.py (role checks on every admin endpoint) |
| — | Extensible workflows + custom step example | test_builder.py, test_custom_step.py |

## Bugs from the inventory (§1.5)

| # | Status |
|---|---|
| 1 | Fixed: page count uses `NOT EXISTS` on mappings |
| 2 | Fixed: embedding status uses a per-collection `LEFT JOIN` |
| 3 | Fixed: the reranker schema allows null ("no match"); see test_null_means_no_match |
| 4 | Fixed: out-of-range ids are validated and re-asked; see test_out_of_range_id_is_reasked |
| 5 | Fixed: Reset deletes overrides |
| 6 | Gone: Streamlit code removed |
| 7 | Fixed: COUNT queries |
| 8 | Fixed: CSV import via COPY into a staging table |
| 9 | Fixed: delete requires a typed confirmation |
| 10 | Fixed: no hidden collection recreation; the collection is created only by embed/migrate |
| 11 | Fixed: `UNIQUE(source_id, concept_id)`, de-duplicated during the migration |

## Not verified here

- **Real provider calls:** OpenAI, Anthropic and others were not called, because no API key was available. Provider wiring is LiteLLM's standard passthrough, and Instructor (tool calling) and the embeddings call path were exercised over HTTP against the fake gateway.
- **Full-size OMOP vocabulary import and DiskANN build times:** not measured; only the fixture vocabulary was used.

## Review follow-up (2026-11-12)

Changes after the code review. Backend: `pytest` 100 passed (`docker compose -f docker-compose.dev.yaml exec backend pytest`).

| Area | Change | Verified by |
|---|---|---|
| Parity | Reranker temperature defaults to 0 again (`LLM_TEMPERATURE`; LiteLLM drops it for reasoning models) | test_accepts_confident_match_with_atc7_filter |
| Parity | ATC7 fallback (search without the ATC7 filter) is off by default, as in the Streamlit app | test_no_atc7_fallback_by_default, test_atc7_fallback_can_be_enabled |
| Parity | Model list: LiteLLM provider wildcards (`openai/*`, `anthropic/*`) make `/v1/models` list concrete models; non-chat models and dated snapshots are filtered | test_gateway_models_are_classified; LiteLLM v1.104 lists 241 `openai/*` models with dummy keys |
| Parity | Domain filtering checks the live `domain_id`; a vocabulary import also prunes and relabels every embedding collection | test_search_filters, test_vocabulary_reimport_swaps_tables_and_keeps_indexes |
| Prompts | Rewritten: one output schema (no conflicting text), candidates with class/domain/vocabulary, explicit 1–10 confidence scale, non-conflicting drug rules, one-sentence rationale stored with suggestions | workflow tests |
| Removed | Qdrant migration task/endpoint/UI and `qdrant-client`; PG15 upgrade path | — |
| Tasks | Locks expire 60 s after a worker dies (heartbeat thread); orphan detection by heartbeat (safe with several workers); launches serialised per group (advisory lock); broker failure → FAILURE + 503 | test_lock_conflict, test_task_lock_released_and_heartbeat_cleared, test_broker_failure_marks_task_failed |
| Tasks | Partial failures end as FAILURE with their result (vocabulary import with a failed table; auto-map where every concept errored) | test_vocabulary_import_with_a_failing_table_fails |
| Tasks | Typed results per task kind, published in OpenAPI and validated when read | all task tests (strict in test mode) |
| Embeddings | Cancel/error still rebuilds the DiskANN index; per-batch staging table (no stale vector size); `DISKANN_MAINTENANCE_WORK_MEM`; retries only on transient errors; status cached 30 s | test_cancelled_embedding_still_builds_the_index, test_bulk_upsert_handles_different_vector_sizes_on_one_connection, test_embedder_retries_only_transient_errors |
| LLM | One async client per event loop, closed at the end of each auto-map task | test_async_client_is_per_event_loop; two consecutive auto-map runs in the live dev worker |
| Import | Vocabulary import no longer locks readers: concept upsert keeps its indexes and writes only changed rows; relationship/ancestor tables are built as shadow tables and swapped in | test_vocabulary_reimport_swaps_tables_and_keeps_indexes, test_reimport_upserts_concepts |
| Mapping | Auto-map (commit mode) never overwrites a mapping made meanwhile; suggestions are not queued for mapped concepts; `GET …/mapped` paginated with search; CSV streamed; uploads stop at 500 MB while streaming | test_source_import_and_mapping_flow |
| Auth | Lockout per username across IPs; uvicorn trusts `X-Forwarded-For` only from `FORWARDED_ALLOW_IPS`; sessions revoked on password/role/status change (`token_version`, migration 0004); constant-time unknown-user path; prod refuses default DB/LiteLLM secrets and short JWT secrets; secure cookie by default in prod | test_auth.py |
| Schema | Alembic autogenerate disabled (SQL migrations are the schema); model columns checked against the migrated DB | test_schema.py |

