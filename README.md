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
| `worker` | Celery: vocabulary import (with ATC7 codes), CSV import, embedding, metadata sync, auto-mapping. |
| `postgres-db` | `timescale/timescaledb-ha:pg18` (PostgreSQL 18 with pgvector 0.8 and pgvectorscale 0.9). Holds the OMOP vocabulary, mappings and vectors (StreamingDiskANN index). |
| `redis` | Celery broker and result backend, cache for read endpoints and query embeddings, task locks and cancel flags. |
| `litellm` | OpenAI-compatible proxy in front of every LLM provider. It is the only service that receives provider API keys (from `.env.litellm`). |

Code layout:

```
backend/     FastAPI app (app/), Alembic migrations, Celery tasks, LLM layer, workflows, tests
frontend/    React app; src/api/schema.d.ts is generated from frontend/openapi.json
examples/    workflow plugin example (custom "translate_query" step)
litellm/     LiteLLM proxy configuration
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
   - Upload the Athena download zip as it is (no unpacking). It must contain `CONCEPT.csv`, `CONCEPT_RELATIONSHIP.csv` and `CONCEPT_ANCESTOR.csv`, and may contain `DRUG_STRENGTH.csv` (deterministic drug mapping) and `CONCEPT_SYNONYM.csv` (synonyms). Tables missing from the zip are skipped. The upload limit is `VOCABULARY_UPLOAD_MAX_MB` (default 20 GB); the zip is deleted after the import.
   - The import runs in the background with a progress bar, and you can leave the page once the upload has finished.
   - Importing a new release updates everything derived from the vocabulary:
     - mappings to concepts that are no longer standard are replaced by their `Maps to` successor, or removed (audit: `auto_vocab_update`, `vocab_removed`);
     - ATC7 codes of drugs are computed (no separate step);
     - every embedding collection drops vectors of concepts that left the standard set, moves domain labels, and flags vectors whose embedded text (name, synonyms, class/domain) changed;
     - the active collection is then re-embedded for changed concepts and new concepts in the domains it already covers (`AUTO_EMBED_AFTER_VOCABULARY_IMPORT`, default on).
2. **Import → Source Concepts**. Upload a comma-delimited CSV:
   - Required columns: `source_value` and `source_concept_name`.
   - Optional: `freq` (empty means 1), and `concept_id` for existing mappings (`;`-separated, for example `1234;5678`).
   - If a `source_value` starts with an ATC7 code (for example `A10BA02…`), drug mapping uses it.
   - Optional drug columns, in the shape of OMOP `drug_strength` (see [Drug mapping](#drug-mapping)).
3. **Import → Embedding Management** (admin). Embeds the standard concepts with the configured embedding model. You can filter by domain and set the batch size.
4. **Search**. Semantic search with domain, vocabulary and ATC7 filters.
5. **Map Concepts**. Work through unmapped source concepts:
   - Pick a concept, edit the search term and filters, select one or more results (or enter concept IDs), then confirm.
   - **AI suggest** runs the mapping pipeline for the selected concept and shows its pick, confidence and step-by-step trace. While it runs, the step it is at is shown (translating, AI pick, re-check by a stronger model, rewriting the search, the agent's searches).
   - **Auto map** runs the pipeline over the whole vocabulary as a background task; its progress line shows how many terms are at each step.
     - In **review** mode (default), results above the confidence threshold become suggestions.
     - In **commit** mode, they are mapped directly, as the old app did.
6. **Review**. Accept or reject suggestions one at a time, as a selection, or all at once above a confidence level.
7. **Check & Commit**. Review the mappings, unmap, download `vocabulary_<id>_mappings.csv`, and view statistics. Admins can delete a source vocabulary.
8. **Configuration**.
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
  - `LLM_TEMPERATURE` defaults to `0`, so reranking is repeatable. Models that reject a temperature (e.g. reasoning models) are fine: the proxy drops unsupported parameters, and models matching `LLM_NO_TEMPERATURE_MODELS` (Claude 5.x by default) never get one. Leave it empty to send no temperature.
  - `LLM_REASONING_EFFORT` (`low` … `max`, empty = the model's default) sets how long the chat model thinks; `RERANK_ESCALATE_EFFORT` does the same for the escalation model.
- **Auto-mapping:** `AUTOMAP_PIPELINE`, `AUTOMAP_CONCURRENCY`, `WORKFLOW_PLUGINS`, `WORKFLOW_DEFINITIONS_DIR`.
  - `RERANK_VOTERS` (two or more models) and `RERANK_JUDGE` turn reranking into a cascade: cheap models pick independently, and only terms they disagree on go to the judge, which sees their picks. In an evaluation on 600 terms, two cheap voters agreed on ~87% of terms, which cut the reranking cost to about a fifth with the same accuracy.
  - `RERANK_ESCALATE_MODEL` (default `anthropic/claude-opus-5-5`, at `RERANK_ESCALATE_EFFORT=xhigh`) reranks again every term where the first answer's confidence is below `RERANK_ESCALATE_BELOW` (default 8), or that found no match or failed. With Sonnet first, about 20% of terms are escalated. In the 600-term evaluation, Opus at xhigh got 0.692 of those terms right against Sonnet's 0.675 (0.877 against 0.873 overall; a small gain, not statistically significant); Opus at medium or high, Claude Fable 5.2 and Sonnet itself at xhigh did no better than Sonnet. Set the model empty to turn escalation off.

### Providers and default models

All LLM traffic goes through the LiteLLM proxy, and models use LiteLLM names (`<provider>/<model>`). [`litellm/config.yaml`](litellm/config.yaml) has a wildcard route for Anthropic, OpenAI, Gemini, Mistral, Voyage, Cohere and Ollama, plus a catch-all `*`. Each route also makes the proxy list that provider's models, which the Configuration page offers. A provider works once its key is in `.env.litellm`.

**Defaults** (chosen November 2026 for mapping accuracy at mid-tier prices and coverage of European languages; the cheapest and the most expensive tiers were left out on purpose):

| Role | Default | Price per 1M tokens | Why |
|---|---|---|---|
| Reranker, translation, agent | `anthropic/claude-sonnet-5-5` | $2 in / $10 out | Mid-tier model of a current family (Opus 5.5 at $4/$20 is the expensive tier); strong multilingual reading and reliable structured output |
| Escalation of unsure terms | `anthropic/claude-opus-5-5` at xhigh effort (`RERANK_ESCALATE_MODEL`, `RERANK_ESCALATE_EFFORT`), below confidence 8 | $4 in / $20 out | Second opinion on the ~20% of terms Sonnet is unsure about; the best of the options tested, including `anthropic/claude-fable-5-2` ($8/$40) |
| Embeddings | `openai/text-embedding-3-large`, `EMBEDDING_DIMS=1536` | $0.13 | Best of the models compared on 856 OMOP terms in five domains (with translation of non-English terms); 3072 dims gained at most one point |

Prices are from the providers' pricing pages; check them before budgeting. Measured cost with up to 30 candidates per term (~2,900 input and ~240 output tokens, default effort): about $8 per 1,000 terms with Sonnet 5.5, plus about $4.50 for escalating ~20% of them to Opus 5.5 at xhigh. The pipelines send up to 30 candidates for every domain (drugs reach the search only when the structured drug_strength match fails); each candidate is roughly 50 input tokens. Higher effort adds output tokens. Translation is extra and cached per term. Embedding the standard vocabulary is a one-off of roughly 12 tokens per concept name (e.g. ~36M tokens, about $7, for 3 million concepts).

**Alternatives** (reranker / embeddings, prices per 1M tokens):

| Setup | `LLM_CHAT_MODEL` | `LLM_EMBEDDING_MODEL` | Notes |
|---|---|---|---|
| Only OpenAI | `openai/gpt-6.1-terra` ($1/$5) | `openai/text-embedding-3-large` ($0.13) | One key |
| Only Google | `gemini/gemini-3.8-flash` ($0.75/$3.75 introductory, $1.50/$7.50 from 2027) | `gemini/gemini-embedding-2` | One key |
| EU provider | `mistral/mistral-large-latest` ($0.50/$1.50) | `mistral/mistral-embed` ($0.10, 1024 dims) | Mistral is EU-based; its embedder is weaker on retrieval than the default |
| Other embedders | — | `voyage/voyage-4-large`, `cohere/embed-v5.0-pro` ($0.12) | Multilingual retrieval models; no independent multilingual scores found |
| Budget | `openai/gpt-6-luna` ($0.10/$0.50) | — | ~20× cheaper; check the accuracy loss on known mappings first |
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

**Languages and translation.** The default `automap` pipeline has a `translate` step: the term is rendered in English OMOP wording (cached per term and model; skipped when it is already English), retrieval searches both the original and the English term and merges the results, and the reranker sees both. In the evaluation this was the largest single gain for noisy Croatian terms (R@1 0.42 → 0.66, R@30 0.90 → 0.97; [report](docs/retrieval-evaluation-2026-11.md)). When the first pass is not confident, an agent searches again with its own wording (see *Mapping pipelines*).

There is no published OMOP-specific benchmark for these models. Before relying on a model choice, run auto-mapping in review mode on a sample with known correct mappings and compare.

### Drug mapping

For drug vocabularies (the vocabulary's drug flag) the default pipeline is `automap_drug` once `DRUG_STRENGTH.csv` is imported (`AUTOMAP_DRUG_PIPELINE`):

1. **Structure.** Taken from the optional CSV columns when present, otherwise extracted from the name by the LLM (ingredients as English INN, strengths, RxNorm dose form). Local names in the columns (`paracetamol`, `tableta`) are translated when they do not match.
2. **Deterministic match** on `drug_strength` in RxNorm and RxNorm Extension, at the level of detail the source has. With a brand name the branded level is tried first, then the clinical one:

   | Source has | Branded (brand given) | Clinical |
   |---|---|---|
   | ingredients + strengths + dose form | Branded Drug | Clinical Drug (else Quant Clinical Drug) |
   | ingredient(s) + strengths | Branded Drug Comp | Clinical Drug Comp, one per ingredient |
   | ingredients + dose form | Branded Drug Form | Clinical Drug Form |
   | ingredients only | Branded Drug Form, if only one exists | Clinical Drug Form, if only one exists; else every Ingredient |

   - A brand without ingredients takes its ingredients from its branded products, when they all have the same ingredient set.
   - "One per ingredient" and "every Ingredient" are combined matches: the source concept is mapped to all of them together, also through the review queue.
   - The ingredient set must be identical, strengths equal after unit conversion (0.5 g = 500 mg; 500 mcg/5 mL = 0.1 mg/mL; 1 % = 10 mg/g or mL; 1 % tolerance) and the dose form identical. RxNorm wins over an RxNorm Extension duplicate.
   - A deterministic match is accepted with confidence 10 (`auto_drug_structured`); the rationale names the level and whether the brand was used.
3. **Fallback.** No match, or several equally good ones: translation, similarity search (tied matches first) and the LLM reranker, which also sees the parsed structure.

CSV example:

```csv
source_value,source_concept_name,ingredient,amount_value,amount_unit,numerator_value,numerator_unit,denominator_value,denominator_unit,dose_form
N02BE01-1,Paracetamol 500 mg tbl,paracetamol,500,mg,,,,,tablet
J01CR02-3,Amoksiklav 875/125,amoxicillin;clavulanate,875;125,mg,,,,,Oral Tablet
N02BE01-7,Paracetamol sirup 120 mg/5 ml,paracetamol,,,120,mg,5,mL,Oral Solution
```

### Mapping pipelines (LangGraph)

Auto-mapping and AI suggest run a declarative pipeline from [`backend/app/workflows/definitions/`](backend/app/workflows/definitions):

- **`automap`** (default). Deterministic steps first, the search and the LLM only for what is left:
  1. `extract_atc7`: ATC7 code at the start of the source value (drug mode).
  2. `match_previous`: the reviewed mapping of another source concept with the same name (case, accents and punctuation ignored). Confidence 10, method `auto_previous`.
  3. `match_code`: the source value looked up in the vocabulary's code systems (**Source codes are**, set at import or with **Edit** next to the vocabulary's description, e.g. `ICD10` for MKB-10), followed through `Maps to`. Confidence 10, `auto_code`.
  4. `translate`: the term in English OMOP wording (see *Languages and translation*).
  5. `match_name`: exact name or synonym (CONCEPT_SYNONYM) of the original term, followed through `Maps to`. Confidence 9, `auto_exact_name`. An exact match of only the English translation is not accepted (the translation may be wrong); it goes to the reranker as its first candidate.
  6. `retrieve`: vector search for up to 30 candidates (`k`), for the original and the English term. Keyword (trigram) search on names and synonyms, fused by reciprocal rank, can be turned on with `keyword: true`; it is off by default because it lowered search accuracy in the evaluation ([report](docs/retrieval-evaluation-2026-11.md)).
  7. `llm_rerank`: the LLM picks a candidate, seeing the original and the English term, with reviewed mappings of similar source terms as examples. It also states the match type (exact / broader / narrower / related) and may map a combined term to several concepts.
  8. `confidence_gate`: accepted at or above the threshold; otherwise
  9. `rewrite_search`: the LLM writes up to three other searches (abbreviations spelled out, synonyms, OMOP wording; cached), and their new candidates go to
  10. a second `llm_rerank`, whose pick replaces the first only when it is more confident, and a second gate; still not confident:
  11. `agentic_refine`: a Pydantic AI agent gets the term, its English rendering and the candidates, and searches again with its own wording (synonyms, other domains, ingredient splits; the ATC7 filter for drugs) and the concept hierarchy. At most `AGENT_REQUEST_LIMIT` model requests (default 6; each tool use is one) and `AGENT_OUTPUT_RETRIES` retries of an invalid answer (default 1). Its pick (method `auto_agentic`) replaces the first one only when it is more confident; if it finds nothing, fails or reaches a limit, the earlier result stands.
  12. a `confidence_gate` with the same threshold; still not confident:
  13. `decide_with_evidence`: the stronger model (`RERANK_ESCALATE_MODEL`) gets every candidate found on the way (first search, rewritten searches, the agent's finds; earlier picks first) and every earlier verdict with its confidence, match type and reason, and makes one final choice (method `auto_final`, shown as *final decision*). If it finds nothing or fails, the earlier result stands.
  14. a last `confidence_gate`.
  Ambiguous deterministic hits (different targets) go to the reranker as the first candidates. Steps 9–14 run only for terms that do not pass the first gate, but they add several LLM calls each. Method codes and steps have readable names in the UI (e.g. `auto_agentic` *Agent retry*, `auto_final` *Final decision*).
- **`automap_drug`** (default for drug vocabularies): the same, with the structured drug match (see [Drug mapping](#drug-mapping)) before translation.

**Domains** (Auto map settings, also used by AI suggest): *only these domains*, *prefer these domains* (default: a few candidates from other domains are added and the reranker is told the preference) or *all domains*.

**Embeddings** (Configuration page): the embedded text can be the name, the name with class and domain, the name with synonyms (default), or both; query/document task types are sent to Gemini, Voyage and Cohere (default on; ignored by other providers). Each combination is its own collection, so variants can be compared without losing the others.

**Review queue**: sort by *Priority* (frequency × uncertainty) to review what affects most records first. After 20 or more decisions, the Review page shows how often suggestions were accepted per confidence level and recommends the threshold that keeps at least 95 % of auto-accepted mappings correct.

**Cost**: identical reranker prompts reuse the cached answer (`LLM_CACHE_TTL`), translations are cached per term, and auto-map decides each distinct source term once per run.

The libraries are used as follows:
- **Instructor** runs the reranker as a one-shot structured call with validation. An out-of-range candidate id is re-asked, and "no match" is allowed.
- **Pydantic AI** runs the tool-using agent, with typed output.
- **LangGraph** composes the steps.

Steps are registered with `@node(...)`/`@router(...)` in a registry, and pipelines are YAML files that refer to those step names. Adding, removing or reordering steps and decision points is a YAML change. New step types come from plugins, with no change to the core code.

#### Example: adding a custom step

[`examples/workflow_plugins/translate_query`](examples/workflow_plugins/translate_query) adds a `translate_query` step: a Pydantic AI agent that rewrites a (for example Croatian) term in English OMOP wording before retrieval. It also adds the `example_translated` pipeline, which uses that step. (Translation itself is built in: the core `translate` step, part of the default `automap` pipeline. The example only shows how a plugin adds a step.)

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
