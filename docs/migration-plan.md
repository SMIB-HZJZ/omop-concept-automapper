<!-- Status: approved 2026-10-05; implemented on branch rewrite/fastapi-react. See docs/feature-checklist.md for the verification checklist. -->

# Migration plan: Streamlit → FastAPI + Celery + React

## Context

`omop-concept-automapper` is a single Streamlit process (`src/frontend` + `src/backend`, ~5.3k LOC). It does three things: imports OMOP vocabularies into Postgres, embeds standard concepts into Qdrant, and maps source concepts to OMOP using vector search plus an LLM reranker.

Problems with the current design:
- Every long operation (vocabulary import, embedding, auto-mapping) blocks the UI thread, and a browser refresh kills it.
- DB connections are module-level and cached with `st.cache_resource`.
- OpenAI is called directly, so the provider is hard-coded.

This rewrite splits the app into a FastAPI backend and a React frontend:
- Background work runs on Celery and is pollable.
- Schema is SQLModel/Alembic.
- All LLM traffic is provider-agnostic.
- Mapping becomes an extensible LangGraph workflow.

The detailed spec is `docs/migration-plan.md` (untracked, from an earlier session). I re-checked it against the code and you confirmed its decisions. This file is the condensed, corrected version. On approval, I'll sync `docs/migration-plan.md` to match and mark it approved.

**Execution mode:** all five phases are implemented end to end without pausing for input (see §6).

**Confirmed decisions**
- **Vector store:** Postgres + pgvector + pgvectorscale (StreamingDiskANN) replaces Qdrant. A one-off task copies the existing Qdrant vectors, so nothing is re-embedded.
- **LLM gateway:** a LiteLLM **proxy** container. Pydantic AI, Instructor and embeddings all use its OpenAI-compatible API.
- **Improvements:** I1–I4, listed in §2.
- **Authentication:** simple auth with two roles, `user` and `admin` (§3.6).
- **Postgres:** upgraded from 15 to **18**, the latest major that pgvectorscale supports. A one-off dump/restore moves the existing data (§3.2).

---

## 1. Inventory of the current app

### 1.1 Features, mapped to phases

| ID | Area | Feature | Phase |
|---|---|---|---|
| F1 | Import › Vocabulary | Row counts for `concept`, `concept_relationship` and `concept_ancestor`, with last import time (`imports.py:get_vocabulary_import_status`, `get_vocabulary_table_counts`) | P1 |
| F2 | Import › Vocabulary | File status: whether `CONCEPT.csv`, `CONCEPT_RELATIONSHIP.csv` and `CONCEPT_ANCESTOR.csv` exist in `vocabulary/`, and their sizes (`check_vocabulary_files_exist`) | P1 |
| F3 | Import › Vocabulary | **Import OMOP vocabulary**. `concept` is upserted via a temp table, with indexes dropped and recreated. Relationship and ancestor use TRUNCATE + COPY (tab-delimited, `QUOTE E'\b'`). Each import is logged to `vocabulary_imports`. Afterwards, `clean_non_standard_mappings` runs. | P2 |
| F4 | Import › ATC7 | **Process ATC7**: an ancestor query gathers the 7-character ATC codes for each standard Drug, then replaces `concept_atc7` (`utils.py:process_drug_atc7_codes`) | P2 |
| F5 | Import › ATC7 | ATC7 statistics: drugs with ATC7, and average codes per drug | P1 |
| F6 | Import › Source | **Upload a source CSV**, with a preview and a vocabulary ID. Columns: `source_value` and `source_concept_name` (required); `freq` (NaN becomes 1); `concept_id` (`;`-separated pre-mappings, checked against `concept`). | P2 |
| F7 | Import › Embeddings | Shows: config, points in the collection, and embedded/total/% of standard concepts (excluding `%box%` and `%marketed%`). Inputs: optional domain filter and a batch-size slider (100–2000). Action: **Embed standard concepts**. | P2 |
| F8 | Import › Embeddings | **Sync standard metadata**: drop vectors of concepts that are no longer standard, and refresh metadata | P2 |
| F9 | Import › Embeddings | **Sync ATC7 codes only** | P2 |
| F10 | Import › Embeddings | **Clear all embeddings** for the current collection | P2 |
| F11 | Search | Query, domains, k (1–100, default 50), vocabulary filter and ATC7 filter. Each result shows rank, score, id, name, domain, class, vocabulary, code and up to 5 ATC7 codes, with tips in the empty state. | P2 (API) / P4 |
| F12 | Map, Commit | Source-vocabulary selector and a "Drug vocabulary" checkbox | P1 / P4 |
| F13 | Map | Metrics (total, mapped, unmapped, %), a progress bar, and an "all mapped" state | P1 |
| F14 | Map | Audit trail: count and average confidence per method, and the last 10 auto-mappings | P1 |
| F15 | Map | Pagination: 25/50/100 per page, page number, "page X of Y" | P1 |
| F16 | Map | **Auto-map all**. Inputs: domains (required), threshold (1–10, default 8), and a drug toggle (defaults to the drug checkbox). Drug mode uses k=30 with ATC7 extraction; standard mode uses k=15. Writes the mapping and an audit row (`auto_drug` / `auto_standard`). | P3 |
| F17 | Map | Unmapped table (code, name, # mapped, freq), sorted by freq descending, single-row select | P1 |
| F18 | Map | Detail panel. **Fields:** editable search name, source id, frequency. **Filters:** domains (kept across concepts), vocabulary, ATC7 toggle, max results (10–200, default 100). **ATC7:** auto-extracted from `source_value`, or entered manually and validated against `^[A-Z]\d{2}[A-Z]{2}\d{2}$`. **Search:** runs automatically on selection and on name/filter change, plus a Search button and a "search method" label. | P4 (API P2) |
| F19 | Map | Results table (multi-select, similarity). Manual concept IDs (`;` or `,`) are looked up, with "not found" errors. | P1 / P4 |
| F20 | Map | Selected-concepts panel and **Confirm mapping**. Confirming replaces the source's existing mappings and writes an audit row with method `manual`. | P1 |
| F21 | Commit | Mapped table (multi-select) and **Unmap selected** | P1 |
| F22 | Commit | **Download CSV** (`vocabulary_{id}_mappings.csv`) | P1 |
| F23 | Commit | Metrics: total mappings, unique OMOP concepts and frequency coverage. Bar chart of mappings by domain. | P1 / P4 |
| F24 | Commit | **Delete source vocabulary** | P1 |
| F25 | Config | Display the current configuration | P1 |
| F26 | Config | Choose the LLM model, embedding model and dimensions (512/1024/1536/2048/3072). Preview the new collection name, and confirm when the embedding settings change. Save; "Reset to defaults" is currently a no-op. | P1 / P3 |
| F27 | Config | Model lists fetched from the provider (cached 1 h), with fallbacks | P3 |
| F28 | Infra | Wait for the DB, apply the schema and log statistics on start (`cli/setup_db.py`) | P1 |
| F29 | Infra | Seed default config (`vector_store.*`, `reranker.model`) | P1 |
| F30 | Infra | Structured logging (`utils/logging.py`) | P1 |
| F31 | Infra | Collection names are derived from model and dims. Note: `text-embedding-3-large` always maps to `omop_vocab`, whatever the dims. The Qdrant migration has to honour this. | P2 |

The Qdrant URL setting (part of F26) is retired by design.

### 1.2 LLM / AI calls

| ID | Today | New home |
|---|---|---|
| L1 Embeddings | `openai.embeddings.create(model, input, dimensions)`. Retries 3× with a 3 s sleep. Used for batch embedding, the query embedding and a `"test"` dimension probe. | `llm/embeddings.py` → proxy `/embeddings`, with retries via tenacity and a Redis query-embedding cache |
| L2 Reranker | `chat.completions.parse` at temperature 0, returning `RerankerResponse{most_similar_item_id:int, confidence_score:1..10}`. Uses a drug or concept system prompt, and candidates are formatted as `i: name`. On error it falls back to candidate[0] with confidence 1. | Instructor structured call inside the LangGraph `llm_rerank` node |
| L3 Model listing | `openai.models.list()` filtered to `gpt*`, with priority models first | `GET /config/models`: env-declared options plus a best-effort proxy `/v1/models`, cached in Redis |

### 1.3 Current data model (`src/backend/db/seed.sql`)

| Table | Contents |
|---|---|
| `source_concepts` | `mapped` is never written |
| `concept` + view `standard_concepts` | OMOP concept table |
| `concept_relationship`, `concept_ancestor` | OMOP tables |
| `concept_atc7` | `atc7_codes text[]` |
| `source_standard_map` | Mappings. No unique constraint. |
| `auto_mapping_audit` | Method, confidence and domains |
| `embedded_concepts` | Tracks the Qdrant collections |
| `app_config` | Key/value settings |
| `vocabulary_imports` | Import log |

### 1.4 Long-running operations, which become Celery tasks

F3, F4, F6, F7, F8, F9 and F16 move to Celery, plus the new Qdrant→pgvector migration. F10 becomes a `DROP TABLE`, so it stays synchronous.

### 1.5 Bugs fixed along the way

1. `get_total_pages` counts `mapped = FALSE`, a column that is never updated, so the page count is wrong.
2. `get_embedding_status` filters on collection in `WHERE` instead of in the `JOIN`. Concepts embedded only in another collection drop out of the total.
3. The prompts allow `null` for "no match", but the schema doesn't, so the answer becomes a parse error and then the fake "confidence 1" fallback.
4. The reranker indexes `candidate_list[id]` without a bounds check.
5. "Reset to defaults" does nothing.
6. `st.error(..., exc_info=True)` raises a `TypeError`.
7. Mapping stats load full row lists just to count them.
8. Source import inserts one row at a time.
9. Deleting a vocabulary has no confirmation.
10. Clearing embeddings silently recreates the collection and leaves stale cached state.
11. `source_standard_map` allows duplicate pairs.

---

## 2. Improvements

**Approved**
- **I1 – `source_vocabulary` table**: `id` (keeps today's integer ids), `name`, `description`, `is_drug`, `created_at`. It is backfilled as "Vocabulary {id}". The drug checkbox becomes a stored default that can be overridden.
- **I2 – Review queue**: auto-map runs in `review` mode (default) or `commit` mode (today's behaviour). Results above the threshold go to `mapping_suggestion` as `pending`. A Review page accepts or rejects one row, a selection, or everything ≥ N.
- **I3 – Per-concept "AI suggest"**: in the Map detail panel, runs the same compiled workflow for one concept without persisting anything, and highlights the pick, its confidence and a trace.
- **I4 – Concurrent auto-map**: `asyncio` with a semaphore of `AUTOMAP_CONCURRENCY` (default 4). It is cancellable and writes progress in batches.

**Smaller items, included unless you object**
- Cancel buttons and a global tray of running tasks.
- Tasks survive a page reload, via `GET /tasks?active=true`.
- A Redis cache for query embeddings.
- Redis locks so only one vocabulary import or embedding run executes at a time.
- A 400 ms search debounce.
- Client-side CSV preview and validation with papaparse.

---

## 3. Architecture

### 3.1 Folder structure

```
backend/
  pyproject.toml uv.lock Dockerfile alembic.ini scripts/{entrypoint.sh,export_openapi.py}
  alembic/versions/{0001_baseline,0002_platform}.py
  app/
    main.py                      # FastAPI, routers, CORS, lifespan
    core/{config,db,redis,cache,logging}.py   # pydantic-settings; SQLModel engine (psycopg3)
    models/  schemas/            # SQLModel tables / request-response DTOs
    api/{deps.py, routes/{config,vocabulary,atc7,source,embeddings,search,concepts,
                          mapping,suggestions,tasks,workflows}.py}
    services/{vocabulary_import,atc7,source_import,mapping,stats,settings_store,qdrant_migration}.py
    vector/{store.py,collections.py}         # VectorStore protocol + PgVectorScaleStore
    llm/{gateway,embeddings,structured,prompts,schemas}.py  agents/{mapping_agent,query_normalizer}.py
    workflows/{state,registry,builder,context,plugins}.py  nodes/{atc7,retrieve,rerank,gates,agentic}.py
              definitions/{automap,automap_refine}.yaml
    worker/{celery_app,progress}.py  tasks/{vocabulary,atc7,source,embeddings,automap,migration}.py
  tests/{conftest.py, api/, workflows/, tasks/}
examples/workflow_plugins/translate_query/{nodes.py,automap_translated.yaml}
frontend/  (Vite + React + TS; openapi.json, src/api/schema.d.ts generated; pages/, components/, hooks/useTask.ts)
(no custom Postgres image: timescale/timescaledb-ha:pg18 ships both extensions)
litellm/config.yaml
```

The SQL and logic in `src/backend/db/methods/*.py` get ported into `services/`, with the prompts copied verbatim from `src/backend/llms/prompt.py`. The old `src/`, the root `Dockerfile`, `requirements.txt`, `run_tests.py` and `tests/` stay untouched until Phase 4 reaches parity, and are removed in Phase 5.

### 3.2 Database schema

- **Postgres image:** `timescale/timescaledb-ha:pg18` at a pinned tag. It ships with pgvector and pgvectorscale, so no custom build is needed. It runs as uid 1000, with `PGDATA=/home/postgres/pgdata/data`, so the `pgdata18` volume mounts at `/home/postgres/pgdata`.
- **Production settings:** `shared_buffers`, `maintenance_work_mem` (important for DiskANN builds), `work_mem`, `max_wal_size` and `effective_cache_size`, all set through `command: -c …` and overridable by env. The healthcheck stays.
- **Data upgrade from PG15 to PG18** (once, scripted, reversible):
  - `scripts/upgrade-postgres.sh` uses a compose profile `pg-upgrade` with a `postgres-15-old` service that mounts the **existing** `pgdata` volume read-only.
  - It runs `pg_dumpall` from PG15 and restores the dump into the new PG18 volume `pgdata18`.
  - It then checks row counts for the key tables. The old `pgdata` volume is left untouched for rollback; deleting it is documented as a manual step.
  - A fresh install skips the script entirely.
- **Migration `0001_baseline`:** matches `seed.sql` exactly. If the entrypoint finds `concept` but no `alembic_version`, as happens after the restore, it stamps `0001`, then runs `upgrade head`. The data itself is unchanged.
- **Migration `0002_platform`:**
  - Enables the `vector` and `vectorscale` extensions.
  - Adds `source_vocabulary` with a backfill and an FK, and drops `source_concepts.mapped`.
  - De-duplicates `source_standard_map`, then adds `UNIQUE(source_id, concept_id)` and `created_at`.
  - Adds `pipeline`, `task_id` and `rationale` to `auto_mapping_audit`.
  - Adds `task_id` to `vocabulary_imports`.
  - Creates `mapping_suggestion`, `task_run`, `embedding_collection`, `domain_label` and `app_user`.
  - Adds nullable `user_id` FKs: `created_by` on `source_standard_map` and `auto_mapping_audit`, `decided_by` on `mapping_suggestion`, and `user_id` on `task_run`. Existing rows stay NULL ("legacy").
  - Renames the `app_config` keys.
- **Migration `0003` (Phase 5):** drops `embedded_concepts`.

**New tables**

| Table | Columns / notes |
|---|---|
| `app_user` | `id`, `username` (unique, case-insensitive), `hashed_password` (argon2), `role` (`user`/`admin`), `is_active`, `created_at`, `last_login_at` |
| `mapping_suggestion` | `id`, `source_id`, `concept_id`, `confidence`, `method`, `pipeline`, `target_domains[]`, `rationale`, `task_id`, `status` (pending/accepted/rejected), `created_at`, `decided_at`. Partial unique index on `source_id` where `status = 'pending'`. |
| `task_run` | `id` (= Celery id), `kind`, `status`, `params jsonb`, `progress_current`, `progress_total`, `message`, `result jsonb`, `error`, timestamps. This is the durable record; Redis holds the live state. |
| `embedding_collection` | `name` (unique), `model`, `dims`, `table_name`, `created_at`. The registry that replaces Qdrant collections. |
| `domain_label` | `domain_id` → `smallint label`, for DiskANN filtered search |
| `emb_<collection>` | Created at runtime, not by Alembic, because the dims vary. Columns: `concept_id PK FK concept ON DELETE CASCADE`, `embedding vector(dims)`, `labels smallint[]`, `embedded_at`. Index: `USING diskann (embedding vector_cosine_ops, labels)`. |

**How metadata works with pgvector**
- Name, domain, class, code and ATC7 are **joined live** at query time, so payloads can no longer go stale.
- F8 becomes "prune non-standard concepts and recompute labels".
- F9 is kept, but now only re-validates and reports counts, since ATC7 is always current.
- Embedding status is a `LEFT JOIN`, which fixes bug 2.

**Search**
- Default: streaming DiskANN with `labels && :domain_labels`, post-filtering on `standard_concept = 'S'` and the optional vocabulary, then `ORDER BY embedding <=> :q LIMIT k`.
- With an ATC7 filter (usually tens of rows): a `MATERIALIZED` CTE filters first, then computes exact distances.

**Vocabulary import:** the worker streams the files with `COPY … FROM STDIN` (psycopg3), keeping the same delimiter and quote settings. Progress is reported as bytes read. Postgres no longer needs the vocabulary mount.

### 3.3 API endpoints (prefix `/api`; task endpoints return `202 {task_id}`)

| Area | Endpoints |
|---|---|
| Health | `GET /health` (checks the DB, Redis and the gateway; public) |
| Auth | `POST /auth/login` (username and password; sets an httpOnly cookie and also returns the token) · `POST /auth/logout` · `GET /auth/me` · `POST /auth/change-password` |
| Users (admin) | `GET/POST /users` · `PATCH /users/{id}` (role, active, reset password) · `DELETE /users/{id}` |
| Config | `GET /config` (effective value and its source: env or override) · `PUT /config` · `POST /config/reset` · `GET /config/models` · `GET /config/collection-preview?model&dims` |
| Vocabulary | `GET /vocabulary/status` · `POST /vocabulary/import` → task |
| ATC7 | `GET /atc7/stats` · `POST /atc7/process` → task |
| Source vocabularies | `GET/POST /source-vocabularies` · `PATCH/DELETE /source-vocabularies/{id}` · `POST /source-vocabularies/{id}/concepts/import` (multipart) → task |
| Mapping: reads | Under `/source-vocabularies/{id}/`: `unmapped?page&per_page` (includes the detected ATC7) · `mapped` · `mapped.csv` · `stats` · `audit/stats` · `audit/recent?limit` |
| Mapping: writes | `POST /mappings {source_id, concept_ids[]}` · `POST /mappings/unmap {source_ids[]}` · `POST /source-vocabularies/{id}/automap {domains, threshold, drug_specific, mode, pipeline?}` → task |
| AI suggest (I3) | `POST /source-concepts/{source_id}/suggest {query?, domains, drug_specific, atc7_codes?, pipeline?}` |
| Review (I2) | `GET /source-vocabularies/{id}/suggestions?status&min_conf&page` · `POST /suggestions/accept` · `POST /suggestions/reject` (by ids, or by `{vocabulary_id, min_confidence}`) |
| Search | `POST /search {query, k, domains, vocabulary_id, atc7_codes}` |
| Concepts | `GET /concepts/{id}` · `GET /concepts?ids=` (returns `found` and `missing`) |
| Embeddings | `GET /embeddings/status?domain` · `POST /embeddings/embed {domain?, batch_size}` · `POST /embeddings/sync-metadata` · `POST /embeddings/sync-atc7` · `DELETE /embeddings` · `POST /embeddings/migrate-from-qdrant {qdrant_url, collection}` |
| Tasks | `GET /tasks/{id}` · `GET /tasks?active=true&kind` · `POST /tasks/{id}/cancel` |
| Workflows | `GET /workflows` (the registered pipelines and nodes) |

### 3.4 Celery tasks (Redis as both broker and result backend)

`worker/progress.py` provides `ProgressReporter`, which tasks use for progress and cancellation:
- `update(current, total, msg)` writes `update_state(PROGRESS)` and, throttled to every 1 s, the `task_run` row.
- `check_cancelled()` reads a Redis flag. Tasks call it between batches.

| Task | Progress unit | Notes |
|---|---|---|
| `vocabulary.import_all` | bytes per table | Concept upsert → relationship → ancestor → clean non-standard mappings. Takes `lock:vocab`, logs to `vocabulary_imports` and invalidates caches. |
| `atc7.process` | phases | Ancestor query, then replaces the table in one transaction |
| `source.import_csv` | rows | COPY to a staging table → insert → validate pre-mappings. Returns the inserted, pre-mapped and invalid-id counts. |
| `embeddings.embed_standard` | concepts | Server-side cursor → gateway → binary COPY. Drops the DiskANN index for runs over 10k rows and rebuilds it as a visible phase. Takes `lock:embed`. |
| `embeddings.sync_metadata`, `embeddings.sync_atc7` | concepts | As described in §3.2 |
| `automap.run` | concepts | I4 concurrency with `review`/`commit` modes. Runs the compiled workflow for each concept, and the task persists the outcome. |
| `migration.qdrant_to_pgvector` | points | Scrolls with `with_vectors=True` → COPY into `emb_<name>` |

### 3.5 Docker Compose (extends the existing `docker-compose.yaml` and `docker-compose.dev.yaml`)

- **`postgres-db`:** `timescale/timescaledb-ha:pg18`. Keeps the same service name and healthcheck. Uses the new `pgdata18` volume at `/home/postgres/pgdata`, with the tuning flags from §3.2. The vocabulary mount is dropped, and in production compose the 5432 port is no longer published.
- **`postgres-15-old`:** only under `profiles: [pg-upgrade]`. It mounts the old `pgdata` volume for the one-off dump.
- **`redis`** (new): `redis:7-alpine` with a healthcheck.
- **`litellm`** (new): a pinned image that mounts `litellm/config.yaml` (wildcard `*` route). It is the only service that holds provider keys.
- **`backend`** (replaces `app`): `entrypoint.sh` runs stamp-if-needed → `alembic upgrade head` → `uvicorn` on port 8000. Mounts `./vocabulary:/data/vocabulary:ro` and the `uploads` volume.
- **`worker`** (new): the same image, running `celery … worker --concurrency=${CELERY_CONCURRENCY}`.
- **`frontend`** (new): Vite build → nginx on **8501** (unchanged URL), proxying `/api` to the backend.
- **`qdrant`:** kept only under `profiles: [qdrant-migration]`, for the one-off copy.
- **Dev compose:** bind mounts, `uvicorn --reload`, Celery under `watchfiles`, the Vite dev server on 5173, and pgAdmin kept.

### 3.6 Authentication and roles

**Mechanism**
- Username and password. Passwords are hashed with argon2 (`pwdlib`).
- Login returns a short-lived **JWT** (HS256, `JWT_SECRET`, default lifetime `JWT_EXPIRE_MINUTES=720`). The token holds `sub`, `role` and `exp`.
- The token is sent as an **httpOnly, `SameSite=Lax` cookie**. Because nginx serves the SPA and `/api` from the same origin, no token is ever exposed to JavaScript. `Secure` is enabled when `COOKIE_SECURE=true`.
- The API also accepts `Authorization: Bearer`, for scripts and tests.
- A Redis counter rate-limits login: 5 failures per username/IP in 15 minutes.
- No self-registration. Admins create users.

**Enforcement**
- `api/deps.py` provides `current_user` (401 if missing or inactive) and `require_admin` (403 otherwise).
- Every router depends on `current_user`, except `/health` and `/auth/login`.
- The user is resolved per request from the DB, so deactivating a user or changing their role takes effect immediately rather than when the token expires.

**Bootstrap.** On startup, if no admin exists, one is created from `ADMIN_USERNAME` and `ADMIN_PASSWORD`. Startup refuses to proceed if `ADMIN_PASSWORD` or `JWT_SECRET` is missing or left at its example value. A CLI is also provided: `python -m app.cli create-user --role admin`.

**Role split**
- **user** (day-to-day mapping work):
  - search
  - view status and stats
  - upload source CSVs
  - manual map and unmap
  - run auto-map
  - AI suggest
  - accept and reject in the review queue
  - CSV export
  - see and cancel their own tasks
- **admin:** everything a user can do, plus:
  - vocabulary import
  - ATC7 processing
  - embed, sync, clear, and migrate from Qdrant
  - change and reset config
  - delete a source vocabulary
  - user management
  - see and cancel all tasks

The split is easy to change per endpoint if you want it different.

**Attribution.** Manual mappings, audit rows, suggestion decisions and tasks record the acting user. The audit trail (F14) and the Review page show who did what.

**Frontend**
- A login page.
- An `AuthProvider` backed by `useQuery(['me'])`. Any 401 redirects to login.
- Route guards; admin-only pages and buttons are hidden for users (the backend enforces regardless).
- An admin-only **Users** page (list, create, change role, deactivate, reset password) and a change-password dialog.

---

## 4. LLM layer

**Gateway.** One `AsyncOpenAI(base_url=LLM_GATEWAY_URL, api_key=LITELLM_MASTER_KEY)`. Models use LiteLLM names, e.g. `openai/gpt-5-mini`, `anthropic/…`, `ollama/…`. `dimensions` is sent only when it is set; otherwise the native size comes from a probe.

**Where Instructor vs Pydantic AI is used**

| Use | Library | Why |
|---|---|---|
| Reranker (L2) | **Instructor** (`instructor.from_openai(gateway)`) | It is a one-shot extraction. `RerankerResponse.most_similar_item_id: int \| None` handles the "no match" case (bug 3). A `field_validator` gets `n_candidates` through the validation context and rejects out-of-range ids, and Instructor re-asks (bug 4). |
| Mapping agent (`agentic_refine` node, also used by I3 when the refine pipeline is picked) | **Pydantic AI** `Agent(output_type=MappingDecision, deps_type=AgentDeps)` | It is multi-turn and uses tools (`search_concepts`, `get_concept`, `get_ancestors`) to reformulate low-confidence matches. Its typed output covers the structured result, so Instructor isn't needed. |
| Query normalizer (custom-step example) | **Pydantic AI** with `output_type=NormalizedQuery` | Shows that a plain typed agent output is enough |

Pydantic AI connects through `OpenAIChatModel(model, provider=LiteLLMProvider(api_base, api_key))`, so it uses the same gateway and naming as everything else.

**LangGraph workflow**
- `AutomapState` holds:
  - inputs: source id, value and name; query; domains; the drug flag; ATC7 codes; threshold
  - outputs: candidates, selected, confidence, rationale, outcome
  - a `trace`
- The graph only **decides**. Persistence is done by the Celery task, or skipped for I3, so batch runs, suggest and tests share one graph.
- **Registry:** `@node(name, params=Model)` and `@router(name, routes={...})`. A `NodeContext` carries the vector store, embedder, Instructor client, agent factory and settings, and is injected so tests can supply fakes.
- **Plugins** are loaded from the `WORKFLOW_PLUGINS` env var (module paths) and from the `omop_mapper.workflow_nodes` entry points.
- **Declarative YAML.** `definitions/automap.yaml` reproduces today's behaviour: `extract_atc7 → retrieve(k_default=15, k_drug=30) → llm_rerank → confidence_gate(accept|reject → END)`. `automap_refine.yaml` routes `reject → agentic_refine → gate2`.
- **Builder** validates the definition:
  - unknown `uses`
  - route labels that don't match what the router declares
  - unreachable nodes
  - missing `END`
  - params, against each node's params model

  It then compiles a `StateGraph`, cached per (pipeline, settings version). Adding, removing or reordering steps means editing YAML plus an optional plugin, with no change to the core code.
- **Custom-step example.** `examples/workflow_plugins/translate_query` registers `@node("translate_query")`, a Pydantic AI agent that turns a (often Croatian) term into English OMOP-style wording. `automap_translated.yaml` inserts it before `retrieve`. It is enabled through `WORKFLOW_PLUGINS` and `WORKFLOW_DEFINITIONS_DIR`, documented in the README, and covered by a test.

**Switching provider by env only.** `LLM_CHAT_MODEL`, `LLM_EMBEDDING_MODEL` and `EMBEDDING_DIMS` are the defaults. The Settings page can override them (F26). Each override records the env baseline it was made against; if the env value later changes, the stale override is ignored and flagged in `GET /config`. "Reset" deletes the overrides (bug 5).

---

## 5. Frontend

**Stack:** Vite, React and TypeScript; Tailwind with shadcn/ui; TanStack Query and Table; react-router; shadcn charts (recharts).

**Typed API.** `backend/scripts/export_openapi.py` writes `frontend/openapi.json`. `openapi-typescript` generates `schema.d.ts` from it, and `openapi-fetch` is the client. `npm run gen:api` regenerates it.

**Task polling.** `useTask(id, {invalidate})` polls `/tasks/{id}` every 1 s until the task reaches a terminal state, then invalidates the given query keys. `TaskTray` reads `/tasks?active=true`.

**Pages**
- **Import:** tabs for Vocabulary, Source concepts, ATC7 and Embeddings (F1–F10)
- **Search:** F11
- **Map:** F12–F20 and I3
- **Review:** I2
- **Commit:** F21–F24
- **Settings:** F25–F27 and a pipeline picker (admin only)
- **Login** and **Users** (admin only): see §3.6

---

## 6. Phases

The phases run back to back, **with no stops for input**. Each phase ends with its own verification: tests, a build, and a smoke run where possible. A failure is fixed before the next phase starts. Each phase is committed on a feature branch `rewrite/fastapi-react`, not on `main`. At the very end, one report covers what was done per phase, the verification results, the feature checklist, and anything that couldn't be verified locally (for example, real LLM calls without API keys).

| Phase | Deliverables | Features |
|---|---|---|
| **P1 Backend foundation** | `backend/` with uv, settings, models, Alembic 0001/0002 including stamp-on-existing. The PG18 + pgvectorscale image, tuning, and the `upgrade-postgres.sh` dump/restore. Redis and backend services in compose, cache, logging. **Auth:** `app_user`, login/logout/me, JWT cookie, role dependencies, admin bootstrap, the users API, and user attribution. CRUD/read endpoints for config, source vocabularies, concepts, unmapped/mapped/stats/audit, map/unmap, CSV export, delete vocabulary, vocabulary status, ATC7 stats, and review accept/reject. Bugs 1, 2, 5, 7, 9, 11. | F1, F2, F5, F12–F15, F17, F19 (lookup), F20–F25, F26 (persist/reset), F28–F30, I1, I2 (data and endpoints) |
| **P2 Background tasks** | Celery app, `worker` service, `task_run`, ProgressReporter, cancel, locks, task endpoints. LiteLLM service, embedder, pgvectorscale store, collections and search. All tasks in §3.4 except automap. Bug 8. | F3, F4, F6–F10, F11 (API), F18 (API), F31 |
| **P3 LLM layer** | Gateway, Instructor reranker (bugs 3 and 4), Pydantic AI agents, workflow registry, builder and YAML, plugins, `automap.run` (I4, I2 modes), the suggest endpoint (I3), `/workflows`, model listing, env-baseline overrides, the translate example | F16, F26 (models), F27, I2 (producer), I3, I4 |
| **P4 Frontend** | Login page, auth guards, the admin Users page, and all other pages with generated types, task polling and the tray; nginx image; frontend in both compose files. Bugs 6, 9 (UI) and 10 become moot. | UI for F1–F27 and I1–I4 |
| **P5 Hardening** | pytest suite, README, `.env.example`, checklist against §1.1, migration 0003, removal of the Streamlit code, `vocabulary/README.md` update | — |

---

## 7. Verification

**Tests.** `docker compose run --rm backend pytest` runs against a throwaway `test_*` database in the compose Postgres, which is needed for vectorscale. It covers:
- **API:** `httpx.AsyncClient` against every router.
- **Auth:** login succeeds and fails, the rate limit triggers, expired and inactive users get 401, each admin-only endpoint returns 403 to a `user`, and the bootstrap admin is created.
- **Celery:** `task_always_eager`.
- **`automap` workflow:** a fake vector store and a mocked Instructor client, covering accept, reject, null match, and an out-of-range id followed by a re-ask.
- **`automap_refine` workflow:** Pydantic AI's `TestModel` and `FunctionModel`.
- **Builder:** validation errors.
- **Translate plugin.**

**End-to-end check (per phase, where it applies)**
1. Run `scripts/upgrade-postgres.sh`, then `docker compose up`. Row counts in PG18 must match PG15 for `concept`, `source_concepts`, `source_standard_map` and `auto_mapping_audit`. The baseline gets stamped and 0002 applied. Then log in as the bootstrap admin, create a `user`, and confirm that the user gets 403 on admin actions.
2. Import the vocabulary and watch its progress.
3. Run ATC7 processing.
4. Upload a source CSV.
5. Migrate from Qdrant (with `--profile qdrant-migration`), or embed one domain.
6. Search.
7. Map a concept manually.
8. Auto-map a small vocabulary in review mode, then accept from the queue.
9. Export the CSV and diff it against the old app's export.
10. Change `LLM_CHAT_MODEL` to another provider in `.env`, restart, and confirm auto-map still works.

**Frontend:** `tsc --noEmit` and `npm run build` pass, and a check confirms the generated types are in sync.

## 8. Risks

- **PG15 → PG18 upgrade:** a full dump/restore of the OMOP tables (millions of rows) takes a while, and `concept_ancestor` is the largest. The old volume stays untouched until you delete it, so rollback means pointing compose back at `pgdata`.
- **Auth scope:** simple local accounts only, with no SSO or OAuth and no refresh tokens. When the token expires, the user logs in again.
- **DiskANN index build:** likely tens of minutes for about 2–3M concepts. It runs as a visible task phase.
- **Embedding `dimensions`:** not every provider supports it. The dims selector is disabled when the probe shows the model ignores it.
- **CORS:** limited to the frontend origin. In production it is effectively same-origin through nginx.
- **New env vars, documented in `.env.example`:** `JWT_SECRET`, `JWT_EXPIRE_MINUTES`, `COOKIE_SECURE`, `ADMIN_USERNAME`, `ADMIN_PASSWORD` and the `PG_*` tuning variables.
