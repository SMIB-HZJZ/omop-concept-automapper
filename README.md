# Auto OMOP Mapper 🗺️

Maps source medical concepts to OMOP standard concepts using vector similarity search (pgvector + pgvectorscale) and LLM reranking. Low-confidence results can get a second pass from a tool-using agent. Results go to a review queue, so a person decides what gets committed.

## 2025 OHDSI Europe Symposium

[Abstract link](https://drive.google.com/file/d/1m-jeBvWbRCNxFuffR210nhSAK-BNqUWF/view?usp=drive_link) ·
[Poster link](https://drive.google.com/file/d/19kEdJ-7Z5XAA33nVR58EvH2oJnZD5ZNY/view?usp=drive_link)

## Architecture

```
browser ──► frontend (nginx, React SPA) ──/api──► backend (FastAPI) ──► PostgreSQL 18 + pgvector + pgvectorscale
                                                     │      ▲
                                            Celery tasks    │ progress / status (task_run)
                                                     ▼      │
                                          Redis (broker, cache) ◄── worker (Celery)
                                                                        │
                                       backend & worker ──► LiteLLM proxy ──► any LLM / embedding provider
```

| Service | What it does |
|---|---|
| `frontend` | React + TypeScript (Vite), Tailwind + shadcn/ui, TanStack Query. Served by nginx on port **8501**, which also proxies `/api` to the backend. |
| `backend` | FastAPI: REST API, authentication, Alembic migrations on start-up. |
| `worker` | Celery: vocabulary import, ATC7 processing, CSV import, embedding, metadata sync, Qdrant migration, auto-mapping. |
| `postgres-db` | `timescale/timescaledb-ha:pg18` (PostgreSQL 18 with pgvector 0.8 and pgvectorscale 0.9). Holds the OMOP vocabulary, mappings and vectors (StreamingDiskANN index). |
| `redis` | Celery broker and result backend, cache for read endpoints and query embeddings, task locks and cancel flags. |
| `litellm` | OpenAI-compatible proxy in front of every LLM provider. It is the only service that holds provider API keys. |

Code layout:

```
backend/     FastAPI app (app/), Alembic migrations, Celery tasks, LLM layer, workflows, tests
frontend/    React app; src/api/schema.d.ts is generated from frontend/openapi.json
examples/    workflow plugin example (custom "translate_query" step)
litellm/     LiteLLM proxy configuration
scripts/     upgrade-postgres.sh (PostgreSQL 15 -> 18 data upgrade)
vocabulary/  put the OMOP vocabulary files here
```

## Quick start

Requirements: Docker with Compose v2, the OMOP vocabulary files from [Athena](https://athena.ohdsi.org/), and an API key for an LLM provider.

```bash
cp .env.example .env
# Edit .env and set at least:
#   POSTGRES_PASSWORD, JWT_SECRET (openssl rand -hex 32), ADMIN_PASSWORD,
#   LITELLM_MASTER_KEY, and your provider key (e.g. OPENAI_API_KEY)
docker compose up -d --build
```

Open <http://localhost:8501> and sign in as `ADMIN_USERNAME` / `ADMIN_PASSWORD`. That admin is created on the first start.

> **Upgrading from the Streamlit version?** Follow the steps in [Upgrading an existing installation](#upgrading-an-existing-installation) first. It moves your existing database to PostgreSQL 18.

## Using the app

1. **Import → OMOP Vocabulary Tables** (admin).
   - Put `CONCEPT.csv`, `CONCEPT_RELATIONSHIP.csv` and `CONCEPT_ANCESTOR.csv` (tab-delimited Athena export) in `vocabulary/`.
   - Start the import. It runs in the background with a progress bar, and you can leave the page.
   - Re-importing upserts concepts and removes mappings to concepts that are no longer standard.
2. **Import → ATC7 Processing** (admin). Computes the 7-character ATC codes of every standard drug. These are used to narrow drug searches.
3. **Import → Source Concepts**. Upload a comma-delimited CSV:
   - Required columns: `source_value` and `source_concept_name`.
   - Optional: `freq` (empty means 1), and `concept_id` for existing mappings (`;`-separated, for example `1234;5678`).
   - If a `source_value` starts with an ATC7 code (for example `A10BA02…`), drug mapping uses it.
4. **Import → Embedding Management** (admin). Embeds the standard concepts with the configured embedding model. You can filter by domain and set the batch size.
5. **Search**. Semantic search with domain, vocabulary and ATC7 filters.
6. **Map Concepts**. Work through unmapped source concepts:
   - Pick a concept, edit the search term and filters, select one or more results (or enter concept IDs), then confirm.
   - **AI suggest** runs the mapping pipeline for the selected concept and shows its pick, confidence and step-by-step trace.
   - **Auto map** runs the pipeline over the whole vocabulary as a background task.
     - In **review** mode (default), results above the confidence threshold become suggestions.
     - In **commit** mode, they are mapped directly, as the old app did.
7. **Review**. Accept or reject suggestions one at a time, as a selection, or all at once above a confidence level.
8. **Check & Commit**. Review the mappings, unmap, download `vocabulary_<id>_mappings.csv`, and view statistics. Admins can delete a source vocabulary.
9. **Configuration**.
   - LLM model, embedding model and dimensions; changes are stored as overrides of the `.env` defaults.
   - The available mapping pipelines.
   - **Users** (admin only): create users, change roles, deactivate and reset passwords.

### Roles

| | user | admin |
|---|---|---|
| Search, map, unmap, auto-map, AI suggest, review, export, upload source CSVs | ✓ | ✓ |
| See and cancel own tasks | ✓ | ✓ (all tasks) |
| Vocabulary import, ATC7, embeddings (embed, sync, clear, Qdrant migration) | | ✓ |
| Change configuration, delete source vocabularies, manage users | | ✓ |

Authentication uses username and password. The JWT travels in an httpOnly `SameSite=Lax` cookie (or an `Authorization: Bearer` header for scripts).

## Configuration

Every setting is an environment variable, and all of them are documented in [`.env.example`](.env.example). The most important groups:

- **Database:** `POSTGRES_*`, plus server tuning via `PG_*` (for example `PG_MAINTENANCE_WORK_MEM` for DiskANN index builds).
- **Auth:** `JWT_SECRET`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `COOKIE_SECURE`.
  - With `APP_ENV=prod` (the default), the app refuses to start while these still hold placeholder values.
- **LLM:** `LLM_CHAT_MODEL`, `LLM_EMBEDDING_MODEL`, `EMBEDDING_DIMS`, the provider keys, and `LITELLM_MASTER_KEY`.
- **Auto-mapping:** `AUTOMAP_PIPELINE`, `AUTOMAP_CONCURRENCY`, `WORKFLOW_PLUGINS`, `WORKFLOW_DEFINITIONS_DIR`.

### Switching the LLM provider

All LLM traffic goes through the LiteLLM proxy, and models use LiteLLM names (`<provider>/<model>`). To switch provider, edit `.env` and restart:

```bash
# Anthropic for reranking/agents, OpenAI for embeddings
LLM_CHAT_MODEL=anthropic/claude-sonnet-5
ANTHROPIC_API_KEY=sk-ant-...
LLM_EMBEDDING_MODEL=openai/text-embedding-3-large
OPENAI_API_KEY=sk-...

# or everything local with Ollama
LLM_CHAT_MODEL=ollama/llama3.1
LLM_EMBEDDING_MODEL=ollama/nomic-embed-text
EMBEDDING_DIMS=            # native size
OLLAMA_API_BASE=http://host.docker.internal:11434
```

```bash
docker compose up -d   # recreates the containers with the new environment
```

Notes:
- Overrides made on the Configuration page remember which `.env` value they replaced. After a provider switch they are ignored automatically and flagged, so no database edit is needed.
- Each embedding model and dimension combination has its own collection. After changing the embedding model, embed again under **Import → Embedding Management**.

### Mapping pipelines (LangGraph)

Auto-mapping and AI suggest run a declarative pipeline from [`backend/app/workflows/definitions/`](backend/app/workflows/definitions):

- **`automap`** (default) reproduces the original behaviour: ATC7 extraction (drug mode), then vector retrieval (k=15, or 30 for drugs), then the LLM reranker, then a confidence gate.
- **`automap_refine`** works the same way, but rejected concepts go to a Pydantic AI agent. The agent reformulates the query (translations, synonyms, ingredient splits) using search and hierarchy tools, and a second gate decides.

The libraries are used as follows:
- **Instructor** runs the reranker as a one-shot structured call with validation. An out-of-range candidate id is re-asked, and "no match" is allowed.
- **Pydantic AI** runs the tool-using agent, with typed output.
- **LangGraph** composes the steps.

Steps are registered with `@node(...)`/`@router(...)` in a registry, and pipelines are YAML files that refer to those step names. Adding, removing or reordering steps and decision points is a YAML change. New step types come from plugins, with no change to the core code.

#### Example: adding a custom step

[`examples/workflow_plugins/translate_query`](examples/workflow_plugins/translate_query) adds a `translate_query` step: a Pydantic AI agent that rewrites a (for example Croatian) term in English OMOP wording before retrieval. It also adds the `automap_translated` pipeline, which uses that step:

```yaml
# examples/workflow_plugins/translate_query/automap_translated.yaml
edges:
  - [extract_atc7, translate]
  - [translate, retrieve]
  ...
```

To enable it, set the following in `.env`:

```bash
WORKFLOW_PLUGINS=examples.workflow_plugins.translate_query.nodes
WORKFLOW_DEFINITIONS_DIR=/app/examples/workflow_plugins/translate_query
# optional: make it the default
AUTOMAP_PIPELINE=automap_translated
```

Then restart the backend and worker. The pipeline appears in the pipeline pickers (Map → Auto map, AI suggest), and on the Configuration page together with the plugin step.

To write your own step:

```python
from app.workflows.registry import node

@node("my_step", description="…")
async def my_step(state, ctx, params) -> dict:
    return {"query": state["query"].lower(), "_summary": "lower-cased the query"}
```

Installed packages can also register steps through the `omop_mapper.workflow_nodes` entry-point group.

## Upgrading an existing installation

The Streamlit version ran PostgreSQL 15 (volume `pgdata`) and Qdrant. To upgrade:

```bash
git pull
cp .env .env.backup && diff .env.example .env   # add the new variables (JWT_SECRET, ADMIN_PASSWORD, LITELLM_MASTER_KEY, LLM_* …)
docker compose build
scripts/upgrade-postgres.sh                      # dump PG15 -> restore into PG18, then verify row counts
docker compose up -d
```

On start-up the backend recognises the restored Streamlit schema. It marks it as the migration baseline and applies the new migrations. These keep all source concepts and mappings, turn source vocabulary ids into named vocabularies, and remove duplicate mapping rows.

Your existing Qdrant vectors can be copied instead of re-embedded:

```bash
docker compose --profile qdrant-migration up -d qdrant
```

Then use **Import → Embedding Management → Migrate from Qdrant**. The old default collection is `omop_vocab`, created with `text-embedding-3-large` at 1024 dimensions, so keep `LLM_EMBEDDING_MODEL=openai/text-embedding-3-large` and `EMBEDDING_DIMS=1024`.

The old `pgdata` volume is never modified. Remove it manually once everything works.

## Development

```bash
docker compose -f docker-compose.dev.yaml up -d --build
```

The dev stack provides:

| What | Where |
|---|---|
| Frontend (Vite dev server with hot reload) | <http://localhost:5173> |
| API with reload | <http://localhost:8000> (docs at `/api/docs`) |
| Worker | restarts on change |
| pgAdmin | <http://localhost:5050> |
| Postgres | `localhost:5455` |

**Without provider keys**, use the fake OpenAI-compatible gateway. It returns deterministic embeddings and reranks by word overlap:

```bash
LLM_GATEWAY_URL=http://fake-llm:4000 docker compose -f docker-compose.dev.yaml --profile fake-llm up -d
```

**Backend tests** (pytest) run against a throwaway database on the dev Postgres, with mocked LLM providers:

```bash
docker compose -f docker-compose.dev.yaml exec backend pytest
```

They cover:
- every router, including auth and role checks
- Celery tasks (run eagerly)
- pgvectorscale search
- the review queue
- the `automap` workflow with a mocked provider behind a real Instructor client (accept, reject, no match, out-of-range re-ask)
- the agent pipeline, using Pydantic AI's `FunctionModel`
- pipeline validation
- the custom-step plugin

**API types for the frontend.** After changing the API:

```bash
docker compose -f docker-compose.dev.yaml exec backend python -m app.cli export-openapi /app/backend/openapi.json
mv backend/openapi.json frontend/openapi.json
cd frontend && npm run gen:api      # regenerates src/api/schema.d.ts
npm run typecheck && npm run build
```

Other backend commands, run with `python -m app.cli <command>`:

| Command | What it does |
|---|---|
| `init` | Wait for the DB, migrate, bootstrap the admin |
| `create-user NAME --role admin` | Create a user |
| `export-openapi [path]` | Write the OpenAPI schema |

## Affiliation

This repository contains work developed as part of my role at the Croatian Institute of Public Health.
