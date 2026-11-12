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
| `worker` | Celery: vocabulary import, ATC7 processing, CSV import, embedding, metadata sync, auto-mapping. |
| `postgres-db` | `timescale/timescaledb-ha:pg18` (PostgreSQL 18 with pgvector 0.8 and pgvectorscale 0.9). Holds the OMOP vocabulary, mappings and vectors (StreamingDiskANN index). |
| `redis` | Celery broker and result backend, cache for read endpoints and query embeddings, task locks and cancel flags. |
| `litellm` | OpenAI-compatible proxy in front of every LLM provider. It is the only service that receives provider API keys (from `.env.litellm`). |

Code layout:

```
backend/     FastAPI app (app/), Alembic migrations, Celery tasks, LLM layer, workflows, tests
frontend/    React app; src/api/schema.d.ts is generated from frontend/openapi.json
examples/    workflow plugin example (custom "translate_query" step)
litellm/     LiteLLM proxy configuration
vocabulary/  put the OMOP vocabulary files here
```

## Quick start

Requirements: Docker with Compose v2, the OMOP vocabulary files from [Athena](https://athena.ohdsi.org/), and an API key for an LLM provider.

```bash
cp .env.example .env                  # app settings: read by backend and worker
cp .env.litellm.example .env.litellm  # provider API keys: read only by the LiteLLM proxy
# In .env set at least (with APP_ENV=prod the app refuses to start on placeholders):
#   POSTGRES_PASSWORD, JWT_SECRET (>= 32 chars: openssl rand -hex 32), ADMIN_PASSWORD, LITELLM_MASTER_KEY
# In .env.litellm set ANTHROPIC_API_KEY and GEMINI_API_KEY (the default models),
# or pick other providers: see "Providers and default models"
docker compose up -d --build
```

Then put the UI behind HTTPS (see [Production deployment](#production-deployment)), open it and sign in as `ADMIN_USERNAME` / `ADMIN_PASSWORD`. That admin is created on the first start.

For a quick local look over plain `http://localhost:8501`, set `COOKIE_SECURE=false` in `.env` (in production the session cookie is HTTPS-only, so login would not stick over HTTP).

This version starts from an empty database: import the vocabulary and embed concepts as described below. It does not migrate data from the Streamlit version.

## Production deployment

- **TLS.** The stack serves plain HTTP on `FRONTEND_PORT` (8501). Terminate TLS in front of it (the institute's reverse proxy, Caddy, Traefik or an nginx with a certificate) and keep `COOKIE_SECURE` unset or `true`. Bind the UI to localhost (`FRONTEND_BIND=127.0.0.1`) when the proxy runs on the same host, so the HTTP port is not reachable from the network.
- **Client addresses.** nginx passes the address it sees as `X-Forwarded-For`, and the backend trusts that header only from the compose network (`APP_SUBNET`, default `172.29.250.0/24`, passed as `FORWARDED_ALLOW_IPS`). The login lockout counts failures per username and address and per username. Behind another reverse proxy, every request comes from the proxy's address, so the per-user limit (`LOGIN_MAX_FAILURES_PER_USER`) is the one that applies unless you configure nginx's `real_ip` module for your proxy.
- **Secrets.** `.env` is read by the backend and worker only, `.env.litellm` by the LiteLLM proxy only, and PostgreSQL receives just its `POSTGRES_*` values. Keep both files out of version control (they are in `.gitignore`).
- **Headers.** nginx sends a Content-Security-Policy, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy` and `Permissions-Policy`, and hides its version. Add `Strict-Transport-Security` on the TLS proxy.
- **Health.** Every service has a healthcheck (the worker answers a Celery ping); `docker compose ps` shows their state.

## Using the app

1. **Import → OMOP Vocabulary Tables** (admin).
   - Put `CONCEPT.csv`, `CONCEPT_RELATIONSHIP.csv` and `CONCEPT_ANCESTOR.csv` (tab-delimited Athena export) in `vocabulary/`. Add `DRUG_STRENGTH.csv` (optional) to enable deterministic drug mapping.
   - Start the import. It runs in the background with a progress bar, and you can leave the page.
   - Re-importing upserts concepts and removes mappings to concepts that are no longer standard.
2. **Import → ATC7 Processing** (admin). Computes the 7-character ATC codes of every standard drug. These are used to narrow drug searches.
3. **Import → Source Concepts**. Upload a comma-delimited CSV:
   - Required columns: `source_value` and `source_concept_name`.
   - Optional: `freq` (empty means 1), and `concept_id` for existing mappings (`;`-separated, for example `1234;5678`).
   - If a `source_value` starts with an ATC7 code (for example `A10BA02…`), drug mapping uses it.
   - Optional drug columns, in the shape of OMOP `drug_strength` (see [Drug mapping](#drug-mapping)).
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
| Vocabulary import, ATC7, embeddings (embed, sync, clear) | | ✓ |
| Change configuration, delete source vocabularies, manage users | | ✓ |

Authentication uses username and password. The JWT travels in an httpOnly `SameSite=Lax` cookie (or an `Authorization: Bearer` header for scripts). Changing a password ends the user's other sessions; an expired session sends the user back to the login page, and then to the page they were on.

## Configuration

Every setting is an environment variable, and all of them are documented in [`.env.example`](.env.example). The most important groups:

- **Database:** `POSTGRES_*`, plus server tuning via `PG_*`, and `DISKANN_MAINTENANCE_WORK_MEM` for DiskANN index builds.
- **Auth:** `JWT_SECRET`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `COOKIE_SECURE`, `LOGIN_MAX_FAILURES`, `LOGIN_MAX_FAILURES_PER_USER`.
  - With `APP_ENV=prod` (the default), the app refuses to start while `JWT_SECRET` (or one shorter than 32 characters), `ADMIN_PASSWORD`, `POSTGRES_PASSWORD` or `LITELLM_MASTER_KEY` still hold placeholder values.
  - `COOKIE_SECURE` defaults to `true` in prod and `false` otherwise.
- **LLM:** `LLM_CHAT_MODEL`, `LLM_EMBEDDING_MODEL`, `EMBEDDING_DIMS`, `LITELLM_MASTER_KEY` (in `.env`) and the provider keys (in `.env.litellm`).
  - `LLM_TEMPERATURE` defaults to `0`, so reranking is repeatable. Models that reject a temperature (e.g. reasoning models) are fine: the proxy drops unsupported parameters. Leave it empty to send no temperature.
- **Auto-mapping:** `AUTOMAP_PIPELINE`, `AUTOMAP_CONCURRENCY`, `WORKFLOW_PLUGINS`, `WORKFLOW_DEFINITIONS_DIR`.

### Providers and default models

All LLM traffic goes through the LiteLLM proxy, and models use LiteLLM names (`<provider>/<model>`). [`litellm/config.yaml`](litellm/config.yaml) has a wildcard route for Anthropic, OpenAI, Gemini, Mistral, Voyage, Cohere and Ollama, plus a catch-all `*`. Each route also makes the proxy list that provider's models, which the Configuration page offers. A provider works once its key is in `.env.litellm`.

**Defaults** (chosen November 2026 for mapping accuracy at mid-tier prices and coverage of European languages; the cheapest and the most expensive tiers were left out on purpose):

| Role | Default | Price per 1M tokens | Why |
|---|---|---|---|
| Reranker, translation, agent | `anthropic/claude-sonnet-5-5` | $2 in / $10 out | Mid-tier model of a current family (Opus 5.5 at $4/$20 is the expensive tier); strong multilingual reading and reliable structured output |
| Embeddings | `gemini/gemini-embedding-2`, `EMBEDDING_DIMS=1536` | $0.20 | Highest MTEB Multilingual score of the hosted APIs checked (69.9, Google's figure); Matryoshka dims 128–3072 (3072 also works with DiskANN, at twice the storage) |

Prices are from LiteLLM's model registry (v1.104) and the providers' pricing pages; check them before budgeting. Rough cost, assuming ~1,150 input and ~80 output tokens per reranked concept: about $31 per 10,000 source concepts with Sonnet 5.5 (translation adds ~20%, and is cached per term). Embedding the standard vocabulary is a one-off of roughly 12 tokens per concept name (e.g. ~36M tokens, about $7, for 3 million concepts).

**Alternatives** (reranker / embeddings, prices per 1M tokens):

| Setup | `LLM_CHAT_MODEL` | `LLM_EMBEDDING_MODEL` | Notes |
|---|---|---|---|
| Only OpenAI | `openai/gpt-6.1-terra` ($1/$5) | `openai/text-embedding-3-large` ($0.13) | One key |
| Only Google | `gemini/gemini-3.8-flash` ($0.75/$3.75 introductory, $1.50/$7.50 from 2027) | `gemini/gemini-embedding-2` | One key |
| EU provider | `mistral/mistral-large-latest` ($0.50/$1.50) | `mistral/mistral-embed` ($0.10, 1024 dims) | Mistral is EU-based; its embedder is weaker on retrieval than the default |
| Other embedders | — | `voyage/voyage-4-large`, `cohere/embed-v5.0-pro` ($0.12) | Multilingual retrieval models; no independent multilingual scores found |
| Budget | `anthropic/claude-haiku-5-5` or `openai/gpt-6-luna` ($0.10/$0.50) | — | ~20× cheaper; check the accuracy loss on known mappings first |
| Local (Ollama) | `ollama/mistral-small` | `ollama/bge-m3` (1024 dims) | No data leaves your network. Needs a GPU for usable speed. For local models set `INSTRUCTOR_MODE=json` if tool calling fails |
| Azure / Vertex / Bedrock | deployment names | deployment names | For EU data residency; add a deployment entry to `litellm/config.yaml` |

Example: everything local with Ollama.

```bash
# .env
LLM_CHAT_MODEL=ollama/mistral-small
LLM_EMBEDDING_MODEL=ollama/bge-m3
EMBEDDING_DIMS=1024
# .env.litellm
OLLAMA_API_BASE=http://host.docker.internal:11434
```

**Languages and translation.** The default `automap` pipeline embeds the source term as is and relies on the multilingual embedding model; the reranker sees the original term next to the English OMOP candidates. The `automap_translated` pipeline adds a `translate` step: the term is rendered in English OMOP wording (cached per term and model), retrieval searches both the original and the English term, and the reranker sees both. `automap_refine` lets an agent reformulate and translate when the first pass is not confident.

There is no published OMOP-specific benchmark for these models. Before relying on a model choice, run auto-mapping in review mode on a sample with known correct mappings and compare.

### Drug mapping

For drug vocabularies (the vocabulary's drug flag) the default pipeline is `automap_drug` once `DRUG_STRENGTH.csv` is imported (`AUTOMAP_DRUG_PIPELINE`):

1. **Structure.** Taken from the optional CSV columns when present, otherwise extracted from the name by the LLM (ingredients as English INN, strengths, RxNorm dose form). Local names in the columns (`paracetamol`, `tableta`) are translated when they do not match.
2. **Deterministic match** on `drug_strength` in RxNorm and RxNorm Extension, at the level of detail the source has:

   | Source has | Target |
   |---|---|
   | ingredients + strengths + dose form | Clinical Drug (else Quant Clinical Drug) |
   | one ingredient + strength | Clinical Drug Comp |
   | ingredients + dose form | Clinical Drug Form |
   | one ingredient | Ingredient |

   The ingredient set must be identical, strengths equal after unit conversion (0.5 g = 500 mg; 500 mcg/5 mL = 0.1 mg/mL; 1 % = 10 mg/g or mL; 1 % tolerance) and the dose form identical. RxNorm wins over an RxNorm Extension duplicate. A unique match is accepted with confidence 10 (`auto_drug_structured`).
3. **Fallback.** No match, or several equally good ones: translation, similarity search (tied matches first) and the LLM reranker, which also sees the parsed structure.

Branded drugs (brand names) are not matched deterministically yet; they go through the fallback.

CSV example:

```csv
source_value,source_concept_name,ingredient,amount_value,amount_unit,numerator_value,numerator_unit,denominator_value,denominator_unit,dose_form
N02BE01-1,Paracetamol 500 mg tbl,paracetamol,500,mg,,,,,tablet
J01CR02-3,Amoksiklav 875/125,amoxicillin;clavulanate,875;125,mg,,,,,Oral Tablet
N02BE01-7,Paracetamol sirup 120 mg/5 ml,paracetamol,,,120,mg,5,mL,Oral Solution
```

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

[`examples/workflow_plugins/translate_query`](examples/workflow_plugins/translate_query) adds a `translate_query` step: a Pydantic AI agent that rewrites a (for example Croatian) term in English OMOP wording before retrieval. It also adds the `example_translated` pipeline, which uses that step. (Translation itself is built in: the core `translate` step and `automap_translated` pipeline. The example only shows how a plugin adds a step.)

```yaml
# examples/workflow_plugins/translate_query/example_translated.yaml
edges:
  - [extract_atc7, translate]
  - [translate, retrieve]
  ...
```

To enable the example, set the following in `.env`:

```bash
WORKFLOW_PLUGINS=examples.workflow_plugins.translate_query.nodes
WORKFLOW_DEFINITIONS_DIR=/app/examples/workflow_plugins/translate_query
# optional: make it the default
AUTOMAP_PIPELINE=example_translated
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
| Redis | `localhost:6390` |
| LiteLLM | `localhost:4000` |

All ports are bound to `127.0.0.1`, so the dev services (pgAdmin has no login) are not reachable from the network. The dev stack runs with `APP_ENV=dev` and `COOKIE_SECURE=false`; `.env.litellm` is optional there.

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

**Frontend checks** (run in `frontend/`, or in a `node:22` container):

```bash
npm run lint          # ESLint (typescript-eslint, react-hooks incl. exhaustive-deps)
npm run typecheck
npm test              # Vitest unit tests for the pure helpers
npm run format:check  # Prettier
npm run build
```

**API types for the frontend.** After changing the API:

```bash
docker compose -f docker-compose.dev.yaml exec backend python -m app.cli export-openapi /app/backend/openapi.json
mv backend/openapi.json frontend/openapi.json
cd frontend && npm run gen:api      # regenerates src/api/schema.d.ts
npm run typecheck && npm run build
```

**CI.** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the backend tests (with Postgres + pgvectorscale and Redis services), Ruff, a check that `frontend/openapi.json` and `schema.d.ts` match the backend, and the frontend lint, typecheck, unit tests and build.

**UI smoke test.** [`frontend/e2e`](frontend/e2e/README.md) has a Playwright walk-through of every page; it changes data, so run it against a test stack.

Other backend commands, run with `python -m app.cli <command>`:

| Command | What it does |
|---|---|
| `init` | Wait for the DB, migrate, bootstrap the admin |
| `create-user NAME --role admin` | Create a user |
| `export-openapi [path]` | Write the OpenAPI schema |

## Affiliation

This repository contains work developed as part of my role at the Croatian Institute of Public Health.
