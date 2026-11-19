# Retrieval and reranking evaluation (November 2026)

Draft for review. Measured on the Athena vocabulary release imported on 2026-11-16 (reranker
follow-up tests on 2026-11-17 and 2026-11-19), with the evaluation scripts described in
[Reproducing](#reproducing). All numbers are from these runs; prices are list prices and must be
checked before they are quoted (see [Caveats](#caveats)).

## Summary and recommendations

The status column says what the app does now (branch `rewrite/fastapi-react`, 2026-11-19).

| # | Finding | Recommendation | Status |
|---|---|---|---|
| 1 | Translating the (noisy) Croatian term to English before search is the largest single gain: OpenAI R@1 0.42 → 0.66, R@30 0.90 → 0.97 (856 terms, p < 0.0001); significant for every embedding model. | Keep translation on by default. | Done (default). |
| 2 | Claude Haiku 5.5 as the translator is 1–2 points behind Sonnet 5.5 (R@1 −0.013, n.s. for OpenAI embeddings; significant for some open models). | Haiku is an acceptable cheaper translator. | Superseded: Haiku is no longer used; Sonnet 5.5 translates. |
| 3 | Embedding concept name + synonyms (the new default) is significantly worse than the name alone (conditions, OpenAI R@1 0.51 vs 0.57). Adding concept class and domain (`name_context`) is also slightly worse (p = 0.006 en, p < 0.001 translated). | **Revert `EMBEDDING_TEXT` default to `name`.** | Done (`EMBEDDING_TEXT=name`). |
| 4 | Fusing keyword search (trigram, names + synonyms) with vector search via RRF lowers R@1 by ~10 points for good embedding models (p < 0.0001) and does not raise R@30. At the app's `k_default = 15`, recall drops 2–3 points (OpenAI 0.955 → 0.926 clean English, 0.950 → 0.930 translated). It helps only Gemini Embedding 2. | Turn `keyword` off by default in the retrieve node (keep it as an option). | Done (`keyword: false` by default). |
| 5 | Best embedding models (pooled, translated Croatian): OpenAI text-embedding-3-large R@1 0.66 / R@30 0.97; Perplexity pplx-embed-v1-4b (open) 0.67 / 0.96, not significantly different. Nemotron 3.5 Embed 8B (open) is clearly better on clean English (R@1 0.67 vs 0.57 on four domains, p < 0.0001) but equal to OpenAI once translation is on. Gemini Embedding 2 (via OpenRouter, without task types) is significantly worse (0.56 / 0.94). | Keep OpenAI text-embedding-3-large as default; pplx-embed-v1-4b or Nemotron are credible self-hostable alternatives. | Done (default). |
| 6 | 3072 vs 1536 dimensions: ≤ 1 point difference. | Keep 1536 (half the storage, HNSW-indexable as `vector`). | Done (default). |
| 7 | Reranker accuracy (600 items, 7 models): Sonnet 5.5 0.873, Opus 5.5 0.870, Gemini 4 Hydrogen 0.865, GPT-6.1 Terra 0.860, gpt-oss-120b 0.860, DeepSeek V4 Pro 0.850, Haiku 5.5 0.832. Only Haiku (p = 0.002) and DeepSeek (p = 0.04) are significantly below Sonnet. | Keep Sonnet 5.5 as default reranker. Opus costs ~2× more for no gain. gpt-oss-120b is the best open-weight option (~20× cheaper than Sonnet) but its confidence scores are poorly calibrated. | Done; see findings 13–16. |
| 8 | Confidence ≥ 8 is a usable auto-accept signal for the Claude models: Sonnet 81 % of items at 0.921 accuracy, Haiku 62 % at 0.922. gpt-oss-120b gives ≥ 8 on 96 % of items (accuracy 0.885), so its confidence separates little. | Calibrate the auto-accept threshold per model; do not reuse one threshold across models. | Open (one threshold, 8). |
| 9 | DiskANN (app default `search_list_size 200`, `rescore 100`) finds 96 % of the exact top 30. Recall is driven mostly by `rescore`: 400/400 gives 0.993 at ~21 ms. HNSW on `halfvec` gives 0.989 at 3 ms (`ef_search 100`) and stays at 0.984–1.0 with domain filters when `hnsw.iterative_scan = relaxed_order` is set. | Raise DiskANN defaults to `search_list_size 400`, `rescore 400`, **or** switch to HNSW halfvec + iterative scan if the ~6× larger index (482 vs 80 MB per 123 k vectors) is acceptable. | Done: DiskANN 400/400 is the default. |
| 10 | Filtered HNSW **without** iterative scan returns too few rows: recall 0.48 for a 14 % filter, every query short. | If HNSW is used, always set `hnsw.iterative_scan`. | Not applicable (DiskANN kept). |
| 11 | DiskANN index builds failed when `maintenance_work_mem` exceeded the container's `/dev/shm` (2 GB vs 512 MB in dev). Production uses `PG_SHM_SIZE` 1g with `DISKANN_MAINTENANCE_WORK_MEM` 1GB, which leaves no headroom. | Set `shm_size` above `maintenance_work_mem` (e.g. 2g) or lower the latter. | Done (`shm_size` 2g in dev and production). |
| 12 | Geography is 12 % of embeddable standard concepts and is not a mapping target for this use case. | Consider excluding the Geography domain from embedding. | Open. |
| 13 | Reasoning effort does not change reranking accuracy: Sonnet 5.5 at medium 0.868, default (high) 0.873, xhigh 0.873; Opus 5.5 at default (medium) 0.870, high 0.863, xhigh 0.872. All differences n.s. | Keep the default effort for the main reranker. | Done (`LLM_REASONING_EFFORT` empty). |
| 14 | The number of candidates (15 / 30 / 60) does not change Sonnet's accuracy on this set (0.875 / 0.873 / 0.877, n.s.); 97 % of its picks are among the top 5. Real terms are harder than the test terms. | Use 30 candidates for every domain (drugs reach the search only when the structured match fails). | Done (`k: 30`). |
| 15 | On the ~20 % of terms Sonnet is unsure about (confidence < 8), every model and effort scores 0.66–0.69 (Sonnet 0.675). Opus 5.5 at xhigh is best (0.692; 0.877 vs 0.873 overall, n.s.); Fable 5.2 (0.675) and Sonnet itself at xhigh (0.667) give nothing. | Escalate unsure terms to Opus 5.5 at xhigh effort (small gain; cost was judged acceptable). | Done (default). |
| 16 | A cascade of two cheap voters (Haiku 5.5 + gpt-oss-120b) that defers to Sonnet only on disagreement reaches Sonnet's accuracy (0.877 vs 0.873, n.s.) at about a sixth of the cost ($1.37 per 1000 terms). Showing the voters' picks to the judge does not help. | Optional low-cost mode. | Available (`RERANK_VOTERS`); off by default. |
| 17 | Most remaining "errors" are acceptable alternatives (often a less specific concept), and the model labels 59 of Sonnet's 76 errors as `exact`. The test set no longer separates the models. | Evaluate further on real reviewed mappings, not on synthetic data. | Open. |

## Setup

### Datasets

| Dataset | Queries | Source of terms | Gold | Candidate pool |
|---|---|---|---|---|
| Conditions | 400 | ICD-10 code names (English) whose SNOMED *Maps to* target is not a trivial name match | the *Maps to* target | 20 000 standard Condition concepts incl. the golds |
| Drugs | 138 | synthetic: an LLM picked commonly used concepts from a random sample and wrote how they appear in source data | picked concept + same-name duplicates (RxNorm / RxNorm Extension) | 15 000 incl. the golds |
| Measurements | 115 | synthetic, as above | as above | 15 000 |
| Procedures | 101 | synthetic, as above | as above | 15 000 |
| Observations | 102 | synthetic, as above | as above | 15 000 |

Marketed Product and Box concepts are excluded from the drug pools, as in the app.

Each term exists in up to six query variants:

| Variant | Meaning |
|---|---|
| `en` | clean English term |
| `hr` | standard Croatian translation (Sonnet 5.5) |
| `hrn` | **noisy Croatian**: clinician-style rewrite (abbreviations, Latin, short forms; Gemini 4 Hydrogen, and GPT-6.1 Terra for procedures/observations after the Gemini free quota ran out) plus programmatic noise: no diacritics (50 %), one typo (25 %), lower case (50 %) |
| `hrn2en` | `hrn` translated to English by Sonnet 5.5 with the app's translation prompt |
| `hrn2en_weak` | the same with Haiku 5.5 |
| `hrn+hrn2en` | the app's behaviour: both the original and the translation are searched and merged |

### Metrics

- **R@k**: share of terms whose gold is among the top k results. R@15 and R@30 matter because
  the reranker saw 15 candidates (30 for drugs) when these tests ran; it now sees 30 for every domain.
- **Significance**: paired sign test on per-term outcomes (better / worse), two-sided.
- **Reranker accuracy**: the pick equals a gold concept.

## Search results

### Embedding models (pooled over 856 terms, 1536 dims or native, vector search only)

R@1 / R@30.

| Model | Open weights | `en` | `hrn` | `hrn+hrn2en` |
|---|---|---|---|---|
| OpenAI text-embedding-3-large (name) | no | 0.60 / 0.98 | 0.42 / 0.90 | **0.66 / 0.97** |
| Perplexity pplx-embed-v1-4b | yes | **0.64** / 0.97 | 0.40 / 0.83 | **0.67** / 0.96 |
| Perplexity pplx-embed-v1-0.6b | yes | 0.60 / 0.95 | 0.35 / 0.80 | 0.63 / 0.95 |
| BGE-M3 | yes | 0.57 / 0.94 | 0.39 / 0.79 | 0.62 / 0.94 |
| Qwen3 Embedding 4B | yes | 0.59 / 0.97 | 0.40 / 0.87 | 0.60 / 0.96 |
| Qwen3 Embedding 8B | yes | 0.58 / 0.98 | 0.41 / 0.89 | 0.59 / 0.96 |
| multilingual-e5-large | yes | 0.56 / 0.95 | 0.34 / 0.78 | 0.56 / 0.92 |
| Gemini Embedding 2 (OpenRouter, no task type) | no | 0.47 / 0.95 | 0.42 / 0.92 | 0.56 / 0.94 |
| OpenAI text-embedding-3-large, name + class + domain | no | 0.60 / 0.97 | 0.42 / 0.89 | 0.64 / 0.96 |

Nemotron 3.5 Embed 8B ran on OpenRouter's free tier and hit its daily limit, so its results are partial:

| Subset | Nemotron R@1 / R@30 | OpenAI R@1 / R@30 | Sign test |
|---|---|---|---|
| `en`, conditions + drugs + measurements + procedures (754) | 0.672 / 0.968 | 0.574 / 0.981 | +195 −79, p < 0.0001 |
| `hrn`, conditions + drugs (538) | 0.558 / 0.937 | 0.392 / 0.911 | +227 −80, p < 0.0001 |
| `hrn+hrn2en`, conditions + drugs (538) | 0.677 / 0.963 | 0.625 / 0.970 | +90 −76, p = 0.31 |

Per domain (OpenAI, `hrn+hrn2en`, R@1 / R@30): conditions 0.62 / 0.96, drugs 0.62 / 1.00,
measurements 0.60 / 0.96, procedures 0.81 / 0.99, observations 0.77 / 0.99. Noisy Croatian
without translation is weakest for measurements (R@30 0.79): lab abbreviations (`S-`, `dU-`)
do not embed well.

### Translation, text variants, keyword search (pooled, sign tests)

| Comparison (OpenAI text-embedding-3-large) | ΔR@1 | ΔR@30 | better / worse | p |
|---|---|---|---|---|
| translation: `hrn+hrn2en` vs `hrn` | +0.242 | +0.074 | 389 / 71 | < 0.0001 |
| Haiku vs Sonnet as translator | −0.013 | −0.007 | 60 / 82 | 0.078 |
| translation only vs original + translation | 0.000 | 0.000 | 1 / 1 | 1.0 |
| name + class + domain vs name (`en`) | −0.002 | −0.014 | 125 / 173 | 0.006 |
| name + class + domain vs name (`hrn+hrn2en`) | −0.023 | −0.013 | 83 / 150 | < 0.0001 |
| vector + keyword (RRF) vs vector (`en`) | −0.098 | −0.008 | 106 / 269 | < 0.0001 |
| vector + keyword (RRF) vs vector (`hrn+hrn2en`) | −0.123 | −0.002 | 117 / 251 | < 0.0001 |

Name + synonyms (conditions only, 400 terms, OpenAI): R@1 0.51 vs 0.57 for the name alone on
`en`, 0.57 vs 0.62 on `hrn+hrn2en`, significantly worse. Embedding synonyms as separate vectors
(best match per concept) gave a small, non-significant gain on conditions.

Keyword search alone (names + synonyms, trigram `word_similarity` ≥ 0.3): R@1 / R@30 0.36 / 0.87
on clean English, 0.14 / 0.44 on noisy Croatian. Searching synonyms as well as names helps keyword
search (+5 points R@1), but the fused result is still worse than vector search alone.

An exact-name lookup (case-insensitive) resolves about a third of the procedure and observation
terms and 3–8 % of drug and lab terms before any search.

## Reranker comparison

600 items: 60 terms per domain, each as clean English and as noisy Croatian with its Sonnet
translation (the app's flow). Candidates are the top 30 from OpenAI text-embedding-3-large
(vector only); the gold is among them in 98.7 % of items. Each model ran through the app's own
`Reranker` (prompt, structured output, temperature 0) with the LLM cache disabled.

| Model | Accuracy | Gold in candidates | `en` | `hrn` | Conf ≥ 8: share / accuracy | Conf < 8 accuracy | p50 latency | Tokens in / out | USD per 1000 terms |
|---|---|---|---|---|---|---|---|---|---|
| Claude Sonnet 5.5 | **0.873** | 0.885 | 0.883 | 0.863 | 0.81 / 0.921 | 0.675 | 2.0 s | 2927 / 239 | 8.25 |
| Claude Opus 5.5 | 0.870 | 0.882 | 0.873 | 0.867 | 0.84 / 0.911 | 0.645 | 2.4 s | 2869 / 265 | 16.77 |
| Gemini 4 Hydrogen (OpenRouter) | 0.865 | 0.877 | 0.867 | 0.863 | 0.82 / 0.909 | 0.667 | 3.3 s | 1647 / 683 | 7.59 |
| GPT-6.1 Terra | 0.860 | 0.872 | 0.867 | 0.853 | 0.85 / 0.906 | 0.604 | 2.9 s | 1495 / 76 | not known |
| gpt-oss-120b (OpenRouter, open weights) | 0.860 | 0.873 | 0.873 | 0.850 | 0.96 / 0.885 | 0.320 | 1.9 s | 1602 / 241 | 0.39 |
| DeepSeek V4 Pro (OpenRouter, open weights) | 0.850 | 0.861 | 0.850 | 0.850 | 0.92 / 0.880 | 0.500 | 2.5 s | 1830 / 162 | 1.89 |
| Claude Haiku 5.5 | 0.832 | 0.843 | 0.823 | 0.840 | 0.62 / 0.922 | 0.686 | 1.3 s | 3100 / 201 | 0.41 |

Against Sonnet 5.5 (sign test on the same 600 items): Opus +10 −12 (p = 0.83), Gemini +8 −13
(p = 0.38), GPT-6.1 Terra +9 −17 (p = 0.17), gpt-oss-120b +12 −20 (p = 0.22), DeepSeek +13 −27
(p = 0.04), Haiku +19 −44 (p = 0.002). Haiku is weakest on drugs (0.78 vs Sonnet 0.89).

Accuracy per domain (Sonnet 5.5): conditions 0.83, drugs 0.89, measurements 0.87, procedures
0.88, observations 0.91.

Qwen3.5 397B (OpenRouter) was dropped after a smoke test: 3 of 4 answers failed the app's
structured-output validation and calls took ~30 s. The direct Gemini API could not be used
(free-tier quota of 20 requests per day); Gemini ran through OpenRouter instead.

**The absolute accuracies are pessimistic.** In 56 of the 600 items at least 5 of the 7 models
picked the same non-gold concept, and inspection shows many are acceptable alternatives: RxNorm
vs RxNorm Extension wording of the same product ("Injection" vs "Injectable Solution"), or a
SNOMED concept as specific as the gold. Any model was correct in 92.2 % of items; all were wrong
in 47. This affects all models equally, so the comparison holds.

Costs use list prices per million tokens (input / output): Opus 5.5 $4 / $20, Sonnet 5.5 $2 / $10,
Haiku 5.5 $0.10 / $0.50 (Anthropic model table cached 2026-11-03); OpenRouter: Gemini 4 Hydrogen
$1.50 / $7.50, DeepSeek V4 Pro $0.90 / $1.50, gpt-oss-120b $0.15 / $0.60 (OpenRouter model list,
2026-11-16). No price was available for GPT-6.1 Terra. Token counts differ between providers
because the tokenizers differ.

## Reranker follow-up: effort, number of candidates, escalation, cascade

Same 600 items. Calls went to the Anthropic API directly (through the LiteLLM proxy), except the
cascade, which used OpenRouter. Effort is sent as `reasoning_effort`, which LiteLLM maps to
Claude's `output_config.effort`; "default" sends none (Sonnet 5.5 and Fable 5.2: high, Opus 5.5:
medium). For the n = 60 run the candidate lists were extended from the same search; their first
30 entries are identical to the lists above, so the n = 30 results are reused.

| Model, effort | Candidates | Accuracy | vs Sonnet default, 30 (better / worse, p) | Output tokens | p50 latency | USD per 1000 terms |
|---|---|---|---|---|---|---|
| Sonnet 5.5, default | 30 | 0.873 | – | 239 | 2.0 s | 8.25 |
| Sonnet 5.5, default | 15 | 0.875 | +7 −6, 1.0 | 234 | 2.1 s | 6.88 |
| Sonnet 5.5, default | 60 | 0.877 | +5 −3, 0.73 | 244 | 2.1 s | 10.85 |
| Sonnet 5.5, medium | 30 | 0.868 | +2 −5, 0.45 | 236 | 2.1 s | 8.23 |
| Sonnet 5.5, xhigh | 30 | 0.873 | +7 −7, 1.0 | 289 | 2.3 s | 8.64 |
| Opus 5.5, default (medium) | 30 | 0.870 | +10 −12, 0.83 | 265 | 2.4 s | 16.77 |
| Opus 5.5, high | 30 | 0.863 | (vs Opus default) +2 −6, 0.29 | 283 | 2.5 s | 17.14 |
| Opus 5.5, xhigh | 30 | 0.872 | (vs Opus default) +4 −3, 1.0 | 339 | 2.7 s | 18.25 |

The gold is within the top 5 / 10 / 15 / 20 / 30 / 60 candidates for 0.943 / 0.975 / 0.985 /
0.987 / 0.987 / 0.990 of the items (all drug items: top 5), so this set cannot show a benefit of
longer lists for harder terms.

**Escalation.** Sonnet (default effort, 30 candidates) is unsure (confidence < 8, no pick or an
error) on 117 items (19.5 %), where it is right on 0.675. Claude Fable 5.2 ($8 / $40 per million
tokens) ran only on a sample: these 117 items plus 60 random items Sonnet was sure about (12 per
domain). The table gives each model's accuracy on the 117 items when it decides them from the same
prompt (without Sonnet's pick), and the resulting overall accuracy.

| Second model | On the 117 unsure items | Overall | USD per 1000 terms (Sonnet + escalation) |
|---|---|---|---|
| none (Sonnet alone) | 0.675 | 0.873 | 8.25 |
| Sonnet 5.5, xhigh | 0.667 | 0.872 | 10.26 |
| Opus 5.5, default / high / xhigh | 0.684 / 0.658 / 0.692 | 0.875 / 0.870 / **0.877** | 12.06 / 12.18 / 12.76 |
| Fable 5.2, default / medium | 0.675 / 0.658 | 0.873 / 0.870 | 16.05 / 15.11 |
| Fable 5.2, default, 60 candidates | 0.667 | – | – |
| Gemini 4 Hydrogen / gpt-oss-120b / GPT-6.1 Terra / DeepSeek V4 Pro / Haiku 5.5 (earlier run) | 0.658 / 0.650 / 0.641 / 0.590 / 0.573 | – | – |

On the 60 control items every Claude model scored 0.97–0.98. The best escalation (Opus at xhigh)
gets 2 more of the 117 items right than Sonnet; the difference is not significant.

**Cascade** (live run through the app's `CascadeReranker`, OpenRouter): Haiku 5.5 and gpt-oss-120b
agreed on 87 % of items, with ~91 % of those right; Sonnet 5.5 decided the rest. Accuracy 0.877
vs Sonnet alone 0.873 (n.s.), $1.37 per 1000 terms. On the items the judge decided, showing it the
voters' picks gave 0.633 against 0.673 without them, so the app's judge prompt is a candidate for
change if the cascade is used.

**Match type.** The reranker also reports how its pick relates to the term. Sonnet's `exact`
picks (551) are counted right in 0.89 of cases at a mean confidence of 8.8; `broader` (29) in
0.79 at 5.1; `related` (10) 0.60 at 4.6; `narrower` (8) 0.38 at 4.9. Low-confidence `broader`
picks therefore go to review as intended, but 59 of Sonnet's 76 errors are labelled `exact`. A
skim of those errors found most of them acceptable (often a less specific concept or an
equivalent product wording).

## Vector indexes

123 325 OpenAI vectors (1536 dims), 800 queries, recall@30 against exact search, PostgreSQL with
pgvector 0.8.6 and pgvectorscale 0.9.1 in the dev container. Exact search takes 1.4 s per query.
Throughput is with 4 parallel clients.

| Index | Setting | Recall@30 | p50 / p95 ms | Queries/s | Size |
|---|---|---|---|---|---|
| HNSW `vector` | ef_search 40 / 100 / 200 | 0.962 / 0.990 / 0.996 | 2.4 / 4.2 / 6.9 | 1146 / 710 / 446 | 963 MB |
| HNSW `halfvec` | ef_search 40 / 100 / 200 | 0.971 / 0.989 / 0.996 | 2.3 / 3.0 / 4.6 | 1274 / 988 / 729 | 482 MB |
| IVFFlat (350 lists) | probes 20 / 50 / 100 | 0.944 / 0.980 / 0.992 | 20 / 48 / 98 | 131 / 54 / 27 | 966 MB |
| DiskANN SBQ (app) | list 200, rescore 100 (**app default**) | 0.960 | 9.0 / 12.4 | 384 | 80 MB |
| DiskANN SBQ (app) | list 200, rescore 200 | 0.981 | 12.1 / 16.4 | 283 | 80 MB |
| DiskANN SBQ (app) | list 400, rescore 400 | 0.993 | 21.2 / 27.7 | 161 | 80 MB |
| DiskANN plain | list 200, rescore 100 | 0.9996 | 24.7 mean | – | 966 MB |

HNSW with `ef_search = 20` returns fewer than 30 rows (recall 0.64): `ef_search` must be at least k.

**Filtered search** (domain filter; labels assigned at random in real domain proportions):

| Index | Filter share | Recall@30 | p50 ms |
|---|---|---|---|
| HNSW halfvec + `iterative_scan = relaxed_order`, ef 100 | 58.6 % / 14.4 % / 6.8 % / 3.5 % / 0.9 % | 0.985 / 0.984 / 0.987 / 0.987 / 1.000 | 3.2 / 5.7 / 9.4 / 14.9 / 6.6 |
| HNSW `vector`, iterative scan **off**, ef 100 | 14.4 % | **0.477**, all queries short | 3.9 |
| HNSW `vector` + iterative scan, ef 100 | 6.8 % / 3.5 % | 1.000 / 1.000 (planner used exact scan) | 54 / 30 |
| DiskANN SBQ with label filter, list 200 rescore 100 | 58.6 % … 0.9 % | 0.946–0.959 | 8.4–11.6 |

At 3072 dims (`halfvec` is required for HNSW above 2000 dims): HNSW halfvec ef 100 recall 0.992 at
6.6 ms; DiskANN SBQ 0.974 at default settings and 0.990 at list 400 / rescore 200.

DiskANN `storage_layout = plain` supports at most 2000 dims and no label filtering, so it is not
an option for the app.

**DiskANN tuning** (follow-up on an idle machine, `shared_buffers` 512 MB, 800 queries,
recall@30 / p50 ms): list/rescore 100/50 0.923 / 5.1; 200/100 0.960 / 7.8; 400/200 0.981 / 13.0;
400/400 0.993 / 19.5. Prewarming the table and index changed nothing measurable (the data was
already cached). Storing vectors inline (`SET STORAGE PLAIN`, avoiding TOAST) cut 400/400 to
16.9 ms. SBQ with 2 bits per dimension is not supported above 900 dimensions. HNSW halfvec on the
same machine: ef 40 / 100 / 200 0.973 / 0.992 / 0.996 at 2.2 / 3.3 / 4.7 ms. The app now uses
DiskANN 400/400: the ~12 ms extra per query is negligible next to the ~2 s LLM call.

Estimated memory at about 2 million concepts (1536 dims): HNSW halfvec needs its index of about
7.8 GB in RAM; DiskANN needs about 1.35 GB for the index, with the ~12 GB of full vectors read
from disk for rescoring. These are extrapolations from the 123 k-vector sizes, not measurements.

## Caveats

- The drug, measurement, procedure and observation sets are **synthetic**. The terms were written by
  Sonnet 5.5, which is also the reranker under test; this may favour Sonnet slightly.
- The conditions set uses official ICD-10 names, which are cleaner than real source terms. The noisy
  Croatian variant is meant to approximate real input, but it is generated, not observed.
- Gold labels are single concepts (plus exact-name duplicates); acceptable alternatives count as
  errors (see the reranker section). Above ~0.87 the reranker results mostly measure this label
  ambiguity, which is why the effort, candidate-count and escalation tests show no clear winner.
- The Fable 5.2 results are from a sample of 177 items (117 hard + 60 control), not all 600.
- **Not tested:** Gemini Embedding 2 through the direct Gemini API with query/document task types
  (free-tier quota exhausted). It may perform better than the OpenRouter result above. A paid
  Gemini key is needed to test it.
- Nemotron results are partial (free-tier daily limit).
- The index tests used random domain labels. Real domains cluster in vector space, which is
  usually easier for filtered graph search, so filtered recall is likely a lower bound.
- Latencies are from the dev machine with one database and no concurrent load besides the test.
- Prices are list prices on the stated dates and must be verified before they are quoted.

## Reproducing

The scripts are in the session scratchpad (`embeval/`); they can be moved to `backend/scripts/eval/`
if the evaluation should be repeatable. Main steps: `build_synth.py` (synthetic samples),
`make_noisy.py` (noisy Croatian and translations), `eval_embeddings.py embed` (embeddings through
the LiteLLM proxy), `analyze.py` and `cross.py` (search metrics and sign tests), `index_bench.py`
and `index_sweep.py`, `tune_bench.py` and `hnsw_ref.py` (indexes), `build_rerank_items.py`
(`TOP=60` for the longer lists), `rerank_bench.py` (run inside the backend container with
`LLM_GATEWAY_URL=http://litellm:4000` and `LLM_CACHE_TTL=0`) and `rerank_analyze.py`;
`cascade_bench.py` / `cascade_analyze.py` (cascade), `topn_sim.py`, `topn_bench.py` and
`topn_analyze.py` (candidates, effort, escalation; `topn_bench.py estimate` prints the cost
before anything runs).
Outputs that contain vocabulary terms are not committed.
