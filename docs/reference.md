# Auto OMOP Mapper — reference

The full reference: deployment, configuration, providers, drug mapping, pipelines and development. The [README](../README.md) has the short version.

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
| `frontend` | React + TypeScript (Vite), Tailwind + shadcn/ui, TanStack Query. Served by nginx over HTTPS (port 443, port 80 redirects), which also proxies `/api` to the backend. |
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
# In .env.litellm set ANTHROPIC_API_KEY and OPENAI_API_KEY (the default models),
# or pick other providers: see "Providers and default models"
docker compose up -d --build
```

Before `docker compose up`, put a certificate in `certs/` (see [Production deployment](#production-deployment)). Then open `https://<server>/` and sign in as `ADMIN_USERNAME` / `ADMIN_PASSWORD`. That admin is created on the first start.

For a local test, a self-signed certificate is enough (the browser will warn about it):

```bash
mkdir -p certs && openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=localhost \
  -keyout certs/privkey.pem -out certs/fullchain.pem
```

This version starts from an empty database: import the vocabulary and embed concepts as described below. It does not migrate data from the Streamlit version.

## Production deployment

- **TLS.** The bundled nginx serves the app over HTTPS on port 443 and redirects port 80 to it. Put the certificate and key in the `certs/` folder next to `docker-compose.yaml`, named `fullchain.pem` (the server certificate followed by the intermediate certificates) and `privkey.pem` (readable by root only is enough: `chmod 600`). The folder is mounted read-only and kept out of git. Point the domain's DNS record at the server. After replacing the certificate, reload nginx: `docker compose exec frontend nginx -s reload`. Keep `COOKIE_SECURE` unset or `true`.
- **Client addresses.** nginx passes the address it sees as `X-Forwarded-For`, and the backend trusts that header only from the compose network (`APP_SUBNET`, default `172.29.250.0/24`, passed as `FORWARDED_ALLOW_IPS`). The login lockout counts failures per username and address and per username. Behind another reverse proxy, every request comes from the proxy's address, so the per-user limit (`LOGIN_MAX_FAILURES_PER_USER`) is the one that applies unless you configure nginx's `real_ip` module for your proxy.
- **Secrets.** `.env` is read by the backend and worker only, `.env.litellm` by the LiteLLM proxy only, and PostgreSQL receives just its `POSTGRES_*` values. Keep both files out of version control (they are in `.gitignore`).
- **Headers.** nginx sends a Content-Security-Policy, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy` and `Permissions-Policy`, `Strict-Transport-Security`, and hides its version.
- **API processes.** `API_WORKERS` (default 2) sets the number of uvicorn processes. Each opens up to 30 database connections, so keep `API_WORKERS` × 30 plus the Celery worker's connections below `PG_MAX_CONNECTIONS` (200).
- **Health.** Every service has a healthcheck (the worker answers a Celery ping); `docker compose ps` shows their state.

## Using the app

1. **Import → OMOP Vocabulary Tables** (admin).
   - Upload the Athena download zip as it is (no unpacking). It must contain `CONCEPT.csv`, `CONCEPT_RELATIONSHIP.csv` and `CONCEPT_ANCESTOR.csv`, and may contain `DRUG_STRENGTH.csv` (deterministic drug mapping), `CONCEPT_SYNONYM.csv` (synonyms) and `VOCABULARY.csv` (vocabulary names and versions; the release version is shown on the Import page). Tables missing from the zip are skipped. The upload limit is `VOCABULARY_UPLOAD_MAX_MB` (default 20 GB); the zip is deleted after the import.
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
   - **Working together.** The term list refreshes every 30 seconds and shows who has a term open; the panel says when a colleague has the same term open. If someone else (or an automatic step) mapped the term meanwhile to other concepts, or to the same ones with another match type, *Confirm mapping* does not overwrite it: a warning names who and when, with **Replace** to overwrite. Confirming exactly what is there changes nothing (the mapping keeps its author); changing your own mapping needs no confirmation (API: `replace` in `POST /api/mappings`, 409 `already_mapped`). An open term stays open when a refresh drops it from the list, with a note, so work in progress is kept.
   - **Match type.** Next to *Confirm mapping*, say how the selected concept(s) relate to the term: *exact* (default), *broader*, *narrower* or *related*. Picking an AI suggestion takes its match type, which can be changed before confirming.
   - In a drug vocabulary the panel shows the term's **drug structure** as auto-mapping reads it: from the CSV columns, or read from the name by the AI (one cached LLM call). It shows the ingredients and strengths, dose form, brand and quantity, how names were read (*paracetamol → acetaminophen*), and the deterministic match: *exact* (with **Use this drug**), *several matches*, *close match* or *no matching drug*. The matched drugs come first in the results. The search runs the way auto-mapping's does: the term and the structure written like an RxNorm name (*acetaminophen 500 MG Oral Tablet*), the ATC7 filter dropped when it finds nothing, and branded drugs left out when the term has no brand (a switch shows them). Text you type in the search box is searched as typed, without these drug additions. **Edit** changes the structure (numbers need their units) and matches it again; **Original** goes back.
   - **AI suggest** runs the mapping pipeline for the selected concept and shows its pick, confidence and step-by-step trace. While it runs, the step it is at is shown (translating, AI pick, re-check by a stronger model, rewriting the search, the agent's searches).
   - **Auto map** runs the pipeline over the whole vocabulary as a background task; its progress line shows how many terms are at each step.
     - In **review** mode (default), results above the confidence threshold become suggestions.
     - In **commit** mode, they are mapped directly, as the old app did.
6. **Review**. Accept or reject suggestions one at a time, as a selection, or all at once above a confidence level.
7. **Check & Commit** (every user). Review the mappings, unmap (users their own, admins any), download `vocabulary_<id>_mappings.csv` and the OMOP `source_to_concept_map`, and view statistics. The flag button on a row adds a question with a note (who and when are kept); **Mappings with a question** lists them (unmapped flagged terms are under Map → Flagged), and clearing the flag marks it settled. Flags live on the source term, so they also work on a released, locked set. The release panel counts open questions and the publish dialog repeats the warning. Admins can delete a source vocabulary.
   - **Releases** (admin). **Publish release N** copies the current mappings into release N (with an optional note) and locks the vocabulary. A database trigger then refuses any change to its mappings and source terms, whichever path tries it (manual mapping, unmapping, accepting suggestions, auto-map, CSV import); the API answers 409. Rejecting suggestions and flagging terms stay possible. **Start release N+1** unlocks it. A change of match type counts as a change. A release with no changes against the previous one is refused, and so is publishing while an auto-map or import of the vocabulary runs.
   - The OMOP `source_to_concept_map` download takes `?release=N` (default: the latest release; without releases, the current mappings) or `?release=draft` (the current mappings as the next release would be). It is built from the copies of every release up to N, per source code (as OMOP rows are keyed): a code mapped to several concepts, or listed twice, has the union of their targets. A (code, concept) pair is valid from the release it first appeared in; one that a later release changed or removed ends the day before that release, with `invalid_reason` `U` (the source code was remapped) or `D` (its mapping was removed). A mapping removed and later added again gets one row per period.
   - **Tools → Export mappings** writes one `source_to_concept_map` for several published vocabularies (`GET /api/source_to_concept_map.csv?vocabulary=<id>[:<release>][:<SOURCE_VOCABULARY_ID>]`, repeated; e.g. `vocabulary=3::HZJZ_DG&vocabulary=5:2:HZJZ_LAB`). Each vocabulary's latest release is used unless a number is given; unreleased vocabularies and drafts are refused. Each needs its own SOURCE_VOCABULARY_ID (default: derived from its name), so codes from different vocabularies stay apart.
   - An OMOP vocabulary import that has to replace or remove mappings of a locked vocabulary (concepts no longer standard) unlocks it first and leaves a note on its draft; the replacements show as vocabulary updates.
8. **Configuration**.
   - LLM model, embedding model and dimensions; changes are stored as overrides of the `.env` defaults.
   - The available mapping pipelines.
   - **Users** (admin only): create users, change roles, deactivate and reset passwords.

The sidebar and the sign-in page show the logos of the Croatian Institute of Public Health (HZJZ, from hzjz.hr) and OHDSI (from ohdsi.org), in `frontend/public/logos/`. They belong to those organisations.

### Roles

| | user | admin |
|---|---|---|
| Search, map, unmap own mappings, auto-map, AI suggest, review, Verify & publish (view, flag, export), upload source CSVs | ✓ | ✓ |
| See and cancel own tasks | ✓ | ✓ (all tasks) |
| Vocabulary import, ATC7, embeddings (embed, sync, clear) | | ✓ |
| Publish and start releases, change configuration, delete source vocabularies, manage users | | ✓ |

**New accounts.** Admins add users on the Users page, or people create their own account on the sign-in page (**Create one**: email and password, at least 8 characters). A self-registered account has the role *user* and cannot sign in until an admin approves it: it is listed under **Waiting for approval** on the Users page (the sidebar shows how many wait), with **Approve** and **Reject** (deletes it). Signing in before that shows "waiting for an administrator's approval", but only with the right password. No email is sent; the address is checked only for its shape. `SELF_REGISTRATION=false` hides the option; `REGISTRATIONS_PER_IP_HOUR` (default 5) limits new accounts per client address.

**Organisation sign-in (Keycloak or another OpenID Connect provider).** With `AUTH_MODE=oidc` the sign-in page has one button, *Sign in with your organisation account*, and the app has no passwords: password sign-in, *Create one*, *Change password*, *Add user* and *Reset password* are gone, and `ADMIN_USERNAME` / `ADMIN_PASSWORD` are not used. The default is `AUTH_MODE=local`.

- **How it works.** The backend runs the authorization code flow with PKCE as a confidential client: it sends the browser to the provider, receives the code at `/api/auth/oidc/callback`, exchanges it with the client secret and verifies the ID token (signature from the provider's keys, issuer, audience, expiry, nonce). It then sets its usual session cookie; the provider's tokens never reach the browser.
- **Accounts.** The first sign-in creates an account (named by the email, linked by the provider's user id, so a changed email keeps the account). It waits under **Waiting for approval** on the Users page like a self-registered one; roles are managed in the app as before. The person whose email is `OIDC_INITIAL_ADMIN` becomes an approved admin on their first sign-in, provided the provider marks the email as verified (otherwise the account waits for approval and the log says why).
- **Logout** ends the app session only; the provider session stays, so on a shared computer also sign out of the provider.
- **Recovery.** If no admin can sign in: `docker compose exec backend python -m app.cli make-admin <username>` approves, activates and promotes an account. If the provider is down, nobody can sign in until it is back; the app and its data are not affected.
- **Keycloak setup.** Create an OpenID Connect client with *Client authentication* on (confidential) and the *Standard flow*; valid redirect URI `https://<host>/api/auth/oidc/callback`. Put the realm's issuer (`https://<keycloak host>/realms/<realm>`), the client id and secret in `.env` as `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, and the first admin's email as `OIDC_INITIAL_ADMIN`. Users need an email in Keycloak (scope `email`).

With `AUTH_MODE=local`, authentication uses username and password. The JWT travels in an httpOnly `SameSite=Lax` cookie (or an `Authorization: Bearer` header for scripts). Changing a password ends the user's other sessions; an expired session sends the user back to the login page, and then to the page they were on.

## Configuration

Every setting is an environment variable, and all of them are documented in [`.env.example`](../.env.example). The most important groups:

- **Database:** `POSTGRES_*`, plus server tuning via `PG_*`, and `DISKANN_MAINTENANCE_WORK_MEM` for DiskANN index builds.
- **Auth:** `JWT_SECRET`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `COOKIE_SECURE`, `LOGIN_MAX_FAILURES`, `LOGIN_MAX_FAILURES_PER_USER`, `SELF_REGISTRATION`, `REGISTRATIONS_PER_IP_HOUR`; for organisation sign-in `AUTH_MODE=oidc` with `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_INITIAL_ADMIN` (see [Roles](#roles)).
  - With `APP_ENV=prod` (the default), the app refuses to start while `JWT_SECRET` (or one shorter than 32 characters), `ADMIN_PASSWORD` (not checked with `AUTH_MODE=oidc`), `POSTGRES_PASSWORD` or `LITELLM_MASTER_KEY` still hold placeholder values. With `AUTH_MODE=oidc` it also refuses to start without the issuer, client id and secret.
  - `COOKIE_SECURE` defaults to `true` in prod and `false` otherwise.
- **LLM:** `LLM_CHAT_MODEL`, `LLM_EMBEDDING_MODEL`, `EMBEDDING_DIMS`, `LITELLM_MASTER_KEY` (in `.env`) and the provider keys (in `.env.litellm`).
  - `LLM_TEMPERATURE` defaults to `0`, so reranking is repeatable. Models that reject a temperature (e.g. reasoning models) are fine: the proxy drops unsupported parameters, and models matching `LLM_NO_TEMPERATURE_MODELS` (Claude 5.x by default) never get one. Leave it empty to send no temperature.
  - `LLM_REASONING_EFFORT` (`low` … `max`, empty = the model's default) sets how long the chat model thinks; `RERANK_ESCALATE_EFFORT` does the same for the escalation model.
- **Auto-mapping:** `AUTOMAP_PIPELINE`, `AUTOMAP_CONCURRENCY`, `WORKFLOW_PLUGINS`, `WORKFLOW_DEFINITIONS_DIR`.
  - `RERANK_VOTERS` (two or more models) and `RERANK_JUDGE` turn reranking into a cascade: cheap models pick independently, and only terms they disagree on go to the judge, which sees their picks. In an evaluation on 600 terms, two cheap voters agreed on ~87% of terms, which cut the reranking cost to about a fifth with the same accuracy.
  - `RERANK_ESCALATE_MODEL` (default `anthropic/claude-opus-5-5`, at `RERANK_ESCALATE_EFFORT=xhigh`) reranks again every term where the first answer's confidence is from `RERANK_ESCALATE_FROM` (default 5) up to below `RERANK_ESCALATE_BELOW` (default 8), or that failed. A pick under 5, or none, means the right concept is most likely not among the candidates; the stronger model cannot fix that, so such terms go straight to the rewritten searches. With Sonnet first, about 20% of terms are escalated. In the 600-term evaluation, Opus at xhigh got 0.692 of those terms right against Sonnet's 0.675 (0.877 against 0.873 overall; a small gain, not statistically significant); Opus at medium or high, Claude Fable 5.2 and Sonnet itself at xhigh did no better than Sonnet. Set the model empty to turn escalation off.

### Providers and default models

All LLM traffic goes through the LiteLLM proxy, and models use LiteLLM names (`<provider>/<model>`). [`litellm/config.yaml`](../litellm/config.yaml) has a wildcard route for Anthropic, OpenAI, Gemini, Mistral, Voyage, Cohere and Ollama, plus a catch-all `*`. Each route also makes the proxy list that provider's models, which the Configuration page offers. A provider works once its key is in `.env.litellm`.

**Defaults** (chosen November 2026 for mapping accuracy at mid-tier prices and coverage of European languages; the cheapest and the most expensive tiers were left out on purpose):

| Role | Default | Price per 1M tokens | Why |
|---|---|---|---|
| Reranker, translation, agent | `anthropic/claude-sonnet-5-5` | $2 in / $10 out | Mid-tier model of a current family (Opus 5.5 at $4/$20 is the expensive tier); strong multilingual reading and reliable structured output |
| Escalation of unsure terms | `anthropic/claude-opus-5-5` at xhigh effort (`RERANK_ESCALATE_MODEL`, `RERANK_ESCALATE_EFFORT`), below confidence 8 | $4 in / $20 out | Second opinion on the ~20% of terms Sonnet is unsure about; the best of the options tested, including `anthropic/claude-fable-5-2` ($8/$40) |
| Embeddings | `openai/text-embedding-3-large`, `EMBEDDING_DIMS=1536` | $0.13 | Best of the models compared on 856 OMOP terms in five domains (with translation of non-English terms); 3072 dims gained at most one point |

**Prompt caching (Anthropic models).** The LiteLLM proxy marks the end of the system message (`cache_control_injection_points` in `litellm/config.yaml`), so the part every call shares (the answer-format tool and the system prompt, about 1,500 tokens for the AI pick) is read from Anthropic's prompt cache at a fraction of the input price instead of billed in full. The prompt itself is unchanged, so answers are the same. Cache entries live 5 minutes and are renewed by each read: a running auto-map keeps them warm, a lone AI suggest after a pause pays a small write premium (1.25× on the shared part). Calls whose shared part is under the model's minimum (512 tokens on Claude 5.5: translation, query rewriting) are not cached.

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

**Languages and translation.** The default `automap` pipeline has a `translate` step: the term is rendered in English OMOP wording (cached per term and model; skipped when it is already English), retrieval searches both the original and the English term and merges the results, and the reranker sees both. In the evaluation this was the largest single gain for noisy Croatian terms (R@1 0.42 → 0.66, R@30 0.90 → 0.97; [report](retrieval-evaluation-2026-11.md)). When the first pass is not confident, an agent searches again with its own wording (see *Mapping pipelines*).

There is no published OMOP-specific benchmark for these models. Before relying on a model choice, run auto-mapping in review mode on a sample with known correct mappings and compare.

### Drug mapping

For drug vocabularies (the vocabulary's drug flag) the default pipeline is `automap_drug` once `DRUG_STRENGTH.csv` is imported (`AUTOMAP_DRUG_PIPELINE`):

1. **Structure.** Taken from the optional CSV columns when present, otherwise extracted from the name by the LLM (ingredients as English INN, strengths, RxNorm dose form, quantity). Names are read in this order:
   - ingredients: the standard Ingredient's name, its RxNorm synonyms, non-standard Ingredients and Precise Ingredients (salts, e.g. *amoxicillin trihydrate*) through `Maps to`, and the INN names of single-ingredient ATC codes (*paracetamol*, *salbutamol*). Accents, case and punctuation are ignored;
   - dose forms: the RxNorm name itself; a form in any other language or abbreviation (*Filmtablette*, *filmom obložena tableta*, *comprimé pelliculé*) is given to the LLM with the list of RxNorm dose forms, and it picks the ones that mean the same (cached; the choice is shown in the rationale). If it finds none, the name is parsed as free text;
   - an ingredient still unknown is translated; one still unknown after that is read as the most similar Ingredient name (trigram similarity ≥ 0.6 and at least 0.1 ahead of the next one).
   - `quantity_value`/`quantity_unit` is the total volume of one unit (a 2 mL ampoule), as the `denominator_value` of an OMOP Quant drug; a component's denominator only expresses its concentration (100 mg per 5 mL).
2. **Deterministic match** on `drug_strength` in RxNorm and RxNorm Extension, at the level of detail the source has. With a brand name the branded level is tried first, then the clinical one:

   | Source has | Branded (brand given) | Clinical |
   |---|---|---|
   | ingredients + strengths + dose form | Branded Drug | Clinical Drug (else Quant Clinical Drug) |
   | ... + quantity | Quant Branded Drug | Quant Clinical Drug with that quantity (else Clinical Drug) |
   | ingredient(s) + strengths | Branded Drug Comp | Clinical Drug Comp, one per ingredient |
   | ingredients + dose form | Branded Drug Form | Clinical Drug Form |
   | ingredients only | Branded Drug Form, if only one exists | Clinical Drug Form, if only one exists; else every Ingredient |

   - A brand without ingredients takes its ingredients from its branded products, when they all have the same ingredient set.
   - "One per ingredient" and "every Ingredient" are combined matches: the source concept is mapped to all of them together, also through the review queue.
   - The ingredient set must be identical, strengths equal after unit conversion (0.5 g = 500 mg; 500 mcg/5 mL = 0.1 mg/mL; 1 % = 10 mg/g or mL; 1 % tolerance) and the dose form identical. RxNorm wins over an RxNorm Extension duplicate.
   - Amount + quantity (10 mg, 2 mL) is compared with a Quant drug's total amount and volume. A concentration without a quantity (5 mg/mL) that only Quant drugs have (2 mL ampoules) is approximate: the source may be another volume.
   - A deterministic match is accepted with confidence 10 (`auto_drug_structured`); the rationale names the level, whether the brand was used and how names were read.
   - **Approximate** matches are not accepted: an ingredient read by similarity, a drug found only in a related dose form (the smallest RxNorm Dose Form Group of the stated form, e.g. *Pill*), or an amount without a quantity compared with Quant drugs' total amount. They go to the reranker as its first candidates, with the reason in its notes.
3. **Fallback.** No match, or several equally good ones: translation, similarity search (tied matches first) and the LLM reranker, which also sees the parsed structure.
   - The similarity search also searches the structure written like an RxNorm name (*acetaminophen 500 MG Oral Tablet*; `retrieve.drug_search_text`, on by default), next to the term and its translation.
   - **Branded drugs** are left out of every search for the term (the first one, rewritten searches, the Map tab) when the term has no brand (`match_drug.skip_branded_without_brand`, on by default; the drug step decides once). There are about 270,000 branded drugs (Branded Drug and Quant Branded Drug) in RxNorm Extension against about 80,000 clinical drugs, and a branded name is the clinical name plus *[Brand]*, so they would fill the candidate list. "No brand" means: the structure has none (empty `brand_name` column, or the parser found none) **and** no RxNorm Brand Name is written in the term (brand names that are also ingredient names, such as *Paracetamol*, do not count). Without a structure (parse failed) branded drugs stay in. Deterministic matches are not affected.

CSV example:

```csv
source_value,source_concept_name,ingredient,amount_value,amount_unit,numerator_value,numerator_unit,denominator_value,denominator_unit,dose_form,quantity_value,quantity_unit
N02BE01-1,Paracetamol 500 mg tbl,paracetamol,500,mg,,,,,tablet,,
J01CR02-3,Amoksiklav 875/125,amoxicillin;clavulanate,875;125,mg,,,,,Oral Tablet,,
N02BE01-7,Paracetamol sirup 120 mg/5 ml,paracetamol,,,120,mg,5,mL,Oral Solution,,
N05BA01-2,Diazepam 10 mg/2 ml amp,diazepam,,,10,mg,2,mL,otopina za injekciju,2,mL
```

**Evaluation.** [`backend/eval/drug_synthetic_cases.csv`](../backend/eval/drug_synthetic_cases.csv) is a synthetic drug list (10 languages; CSV structure, free text, typos, brands, Quant drugs, non-drugs) with the expected RxNorm concepts. `docker compose -f docker-compose.dev.yaml exec -T backend python scripts/eval_drug_pipeline.py` runs it through the real pipeline and LLMs and prints accuracy, wrong auto-accepts and results by kind, method and language. The same logic with fake LLMs runs in the test suite (`tests/workflows/test_drug_synthetic.py`). On 2026-11-23 (Sonnet 5.5, Opus 5.5 escalation, all standard concepts except Box and Marketed Product embedded): 73 of 75 correct, 2 accepted against the expected answer, both judgment calls: the brand "Adrenalin" without the stated 1 mL ampoule instead of the generic 1 mL drug, and "budesonide 0.2 MG Inhalation Powder" (the source's wording) instead of the per-actuation Dry Powder Inhaler. An expected answer may be `NONE|<concept>` when leaving a non-drug unmapped and mapping it to a device are both right. The set is synthetic and was written alongside the code, so it shows that the steps work, not how accurate mapping of a real list is.

With an ATC7 code in the source value, the drug's similarity search is filtered by it; when the filter leaves no candidates (a wrong or outdated code), the search runs again without it (`atc7_fallback`, on by default).

### Mapping pipelines (LangGraph)

Auto-mapping and AI suggest run a declarative pipeline from [`backend/app/workflows/definitions/`](../backend/app/workflows/definitions):

- **`automap`** (default). Deterministic steps first, the search and the LLM only for what is left:
  1. `extract_atc7`: ATC7 code at the start of the source value (drug mode).
  2. `match_previous`: the reviewed mapping of another source concept with the same name (case, accents and punctuation ignored). Confidence 10, method `auto_previous`.
  3. `match_code`: the source value looked up in the vocabulary's code systems (**Source codes are**, set at import or with **Edit** next to the vocabulary's description, e.g. `ICD10` for MKB-10), followed through `Maps to`. Confidence 10, `auto_code`.
  4. `match_name`: exact name or synonym (CONCEPT_SYNONYM) of the original term, followed through `Maps to`. Confidence 9, `auto_exact_name`. Runs before translating, so exact hits need no translation.
  5. `translate`: the term in English OMOP wording (see *Languages and translation*); `match_english` then checks the English term's exact name: such a hit is not accepted (the translation may be wrong) but becomes the reranker's first candidate.
  6. `retrieve`: vector search for up to 30 candidates (`k`), for the original and the English term. Keyword (trigram) search on names and synonyms, fused by reciprocal rank, can be turned on with `keyword: true`; it is off by default because it lowered search accuracy in the evaluation ([report](retrieval-evaluation-2026-11.md)).
  7. `llm_rerank`: the LLM picks a candidate, seeing the original and the English term, with reviewed mappings of similar source terms as examples. It also states the match type (exact / broader / narrower / related) and may map a combined term to several concepts.
     Every mapping carries a **match type**. The AI steps (pick, re-check, rewritten searches, agent, final decision) state it; code, name and earlier-mapping matches are *exact*; a structured drug match derives it from the matched level (*exact* when the drug states what the source states, *broader* when it leaves out a brand or quantity, *narrower* when it adds detail, e.g. the one dose form of an ingredient-only source). Manual mappings are *exact* unless the mapper chooses otherwise; mappings from the import CSV have none. It is kept with the mapping, in releases and the audit trail, shown on Verify & publish and exported as the last column of the mappings CSV (OMOP `source_to_concept_map` has no column for it).
  8. `confidence_gate`: accepted at or above the threshold; otherwise
  9. `rewrite_search`: the LLM writes up to three other searches (abbreviations spelled out, synonyms, OMOP wording; cached), and their new candidates go to
  10. a second `llm_rerank` (method `auto_rewritten`) with the first model only (no escalation), whose 10 best candidates are shown with their parents, defining attributes (finding site, morphology, LOINC component and system, RxNorm ingredient and dose form, ...) and English synonyms; its pick replaces the first only when it is more confident; a second gate; still not confident:
  11. `agentic_refine`: a Pydantic AI agent gets the term, its English rendering and the candidates, and searches again with its own wording (synonyms, other domains, ingredient splits; the ATC7 filter for drugs). Its `get_concept_details` tool returns a concept's synonyms, parents, children and defining attributes. At most `AGENT_REQUEST_LIMIT` model requests (default 6; each tool use is one) and `AGENT_OUTPUT_RETRIES` retries of an invalid answer (default 1). Its pick (method `auto_agentic`) replaces the first one only when it is more confident; if it finds nothing, fails or reaches a limit, the earlier result stands.
  12. a `confidence_gate` with the same threshold; still not confident:
  13. `decide_with_evidence`: the stronger model (`RERANK_ESCALATE_MODEL`) gets every candidate found on the way (first search, rewritten searches, the agent's finds; earlier picks first, the 10 first with the same context as step 10) and every earlier verdict with its confidence, match type and reason, and makes one final choice (method `auto_final`, shown as *final decision*). If it finds nothing or fails, the earlier result stands.
  14. a last `confidence_gate`.
  Ambiguous deterministic hits (different targets) go to the reranker as the first candidates. Steps 9–14 run only for terms that do not pass the first gate, but they add several LLM calls each; the stronger model is called at most twice per term (the re-check in step 7 and the final decision). Picks from steps 9–14 (methods `auto_rewritten`, `auto_agentic`, `auto_final`) always go to Review, also in *Commit directly* mode: a confidence reached after several failed attempts is not yet known to be as reliable as a first pick's (Review's precision by method will show). Every suggestion keeps its decision path, e.g. *AI pick 6 → agent retry 7 → final decision 8*, shown in Review, the AI suggest result and the Map panel. Method codes and steps have readable names in the UI (e.g. `auto_agentic` *Agent retry*, `auto_final` *Final decision*).
- **`automap_drug`** (default for drug vocabularies): the same, with the structured drug match (see [Drug mapping](#drug-mapping)) before translation.

**Domains and vocabularies** (Auto map settings; AI suggest uses the Map panel's domain and vocabulary filters): target domains and target OMOP vocabularies, both optional (none chosen: any domain, any vocabulary; e.g. LOINC for lab tests; the list shows the vocabularies with standard concepts in the chosen domains), and one setting for both:
- *Only these*: every search (first, rewritten, the agent's) is filtered by both; deterministic matches (code, name, earlier mapping, drug structure) outside them are not accepted. Only RxNorm for a drug list leaves out RxNorm Extension, where many European drugs are; the popover warns.
- *Preferred* (default): searches are filtered, up to 5 candidates from outside are added (`retrieve.other_domains`) and the reranker and agent are told the preference; deterministic matches are kept.
- *All*: neither is used.

Auto-map always runs the default pipeline of the vocabulary type (`AUTOMAP_PIPELINE` or `AUTOMAP_DRUG_PIPELINE`); AI suggest can still pick another one for a single term.

**Embeddings** (Configuration page): the embedded text can be the name, the name with class and domain, the name with synonyms, or both (default: the name alone, `EMBEDDING_TEXT=name`); query/document task types are sent to Gemini, Voyage and Cohere (default on; ignored by other providers). Each combination is its own collection, so variants can be compared without losing the others.

**Review queue**: sort by *Priority* (frequency × uncertainty) to review what affects most records first. After 20 or more decisions, the Review page shows how often suggestions were accepted per confidence level and recommends the threshold that keeps at least 95 % of auto-accepted mappings correct. Terms mapped by hand in Map count too: an AI pick for the term (a suggestion waiting in Review, or a pick under the threshold shown as a hint) counts as accepted when the term is mapped to exactly its concepts and as rejected otherwise, so the calibration also covers confidence levels under the threshold and can recommend lowering it. Mapping the term again re-decides the pick; removing the mapping (Undo) puts the pick back and takes it out of the calibration.

**Cost**: identical reranker prompts reuse the cached answer (`LLM_CACHE_TTL`), translations are cached per term, and auto-map decides each distinct source term once per run.

The libraries are used as follows:
- **Instructor** runs the reranker as a one-shot structured call with validation. An out-of-range candidate id is re-asked, and "no match" is allowed.
- **Pydantic AI** runs the tool-using agent, with typed output.
- **LangGraph** composes the steps.

Steps are registered with `@node(...)`/`@router(...)` in a registry, and pipelines are YAML files that refer to those step names. Adding, removing or reordering steps and decision points is a YAML change. New step types come from plugins, with no change to the core code.

#### Example: adding a custom step

[`examples/workflow_plugins/translate_query`](../examples/workflow_plugins/translate_query) adds a `translate_query` step: a Pydantic AI agent that rewrites a (for example Croatian) term in English OMOP wording before retrieval. It also adds the `example_translated` pipeline, which uses that step. (Translation itself is built in: the core `translate` step, part of the default `automap` pipeline. The example only shows how a plugin adds a step.)

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

**CI.** [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs the backend tests (with Postgres + pgvectorscale and Redis services), Ruff, a check that `frontend/openapi.json` and `schema.d.ts` match the backend, and the frontend lint, typecheck, unit tests and build.

**UI smoke test.** [`frontend/e2e`](../frontend/e2e/README.md) has a Playwright walk-through of every page; it changes data, so run it against a test stack.

Other backend commands, run with `python -m app.cli <command>`:

| Command | What it does |
|---|---|
| `init` | Wait for the DB, migrate, bootstrap the admin |
| `create-user NAME --role admin` | Create a user |
| `export-openapi [path]` | Write the OpenAPI schema |

## Affiliation

This repository contains work developed as part of my role at the Croatian Institute of Public Health.
