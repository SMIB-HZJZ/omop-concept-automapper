"""Prompts for the reranker and the refinement agent.

Rewritten from the Streamlit-era prompts:
- The output format is defined once, by the structured-output schema (``RerankerResponse``), instead
  of a free-text "Output Format" section that contradicted it (string vs integer ids, null allowed
  in one prompt but not the other).
- Candidates are shown with their concept class, domain and vocabulary, which the rules depend on
  (branded vs clinical drug, ingredient vs product, condition vs observation).
- The confidence scale is defined explicitly, so the auto-map threshold (default 8) has a stable
  meaning across models.
- The drug rules no longer conflict: "dosage must be identical" vs "prefer less specific" is now
  "match the level of detail the source term has".
- Static text comes first and the variable part last, so providers can cache the prefix.
"""

CONFIDENCE_SCALE = """\
Confidence scale (integer 1-10):
- 10: same concept, every stated attribute matches; no reasonable doubt.
- 8-9: same concept; only wording, spelling, language or unit notation differs.
- 6-7: probably the same concept, but one attribute is uncertain or slightly less specific.
- 3-5: related concept only (broader/narrower, different form or strength); needs human review.
- 1-2: weak guess.
If no candidate is at least related, select nothing (null) instead of a low score."""

DRUG_CRITERIA = """\
Matching rules for drugs:
1. Active ingredients must be identical. Treat recognised synonyms as identical
   (e.g. paracetamol = acetaminophen, adrenaline = epinephrine, salbutamol = albuterol).
2. Match the level of detail the source term has. If the source states a strength, the strength
   must be equal after unit conversion (e.g. 500 mcg/5 mL = 0.1 mg/mL). If it states no strength,
   choose a candidate without one (Clinical Drug Form or Ingredient), never a guessed strength.
   Apply the same rule to dose form and route.
3. Dose form and route must be compatible (tablet vs film-coated tablet is acceptable;
   tablet vs injection is not).
4. Branded candidates (class contains "Branded") are allowed only if the source term names
   that brand. Otherwise prefer the clinical (generic) equivalent.
5. When several candidates satisfy all rules, choose the least specific one that still keeps
   every attribute stated in the source term."""

CONCEPT_CRITERIA = """\
Matching rules for clinical concepts:
1. The candidate must mean the same thing as the source term: the same condition, procedure,
   measurement or observation, not merely a related one.
2. Respect the domain: a diagnosis must not map to a procedure or a lab test, and vice versa.
3. Never choose a candidate more specific than the source term (no added laterality, severity,
   stage, site or cause that the source does not state). If no exact match exists, a slightly
   broader correct concept is acceptable, with a lower confidence.
4. Judge meaning, not wording: synonyms, abbreviations, word order, spelling variants and
   translations are equivalent."""

_RERANK_TASK = """\
You map terms from a medical source vocabulary to standard OMOP concepts.
The source term may be in a language other than English, abbreviated or misspelled; the
candidates are standard OMOP concepts in English, found by semantic search and listed as
`<id>: <concept name> [<concept class> | <domain> | <vocabulary>]`.

Select the id of the candidate that best matches the source term under the rules below, rate
your confidence on the scale below, and give a one-sentence rationale naming the attribute that
decided it. Select only ids from the list. Select nothing if no candidate fits.

Also state how the selected concept relates to the source term: exact (same meaning), broader
(more general), narrower (more specific), or related. If the source term combines several
concepts that OMOP keeps separate (e.g. a condition and its manifestation), list the ids of the
further candidates that must be mapped together with the first; otherwise leave that list empty.

Reviewed mappings of similar source terms, when given, show the conventions to follow."""

DRUG_SYSTEM_PROMPT = f"{_RERANK_TASK}\n\n{DRUG_CRITERIA}\n\n{CONFIDENCE_SCALE}\n"

CONCEPT_SYSTEM_PROMPT = f"{_RERANK_TASK}\n\n{CONCEPT_CRITERIA}\n\n{CONFIDENCE_SCALE}\n"

INPUT_PROMPT = """\
{examples}Source term: {input_term}
{translation_line}
Candidates:
{candidate_list}"""

EXAMPLES_BLOCK = "Reviewed mappings of similar source terms:\n{lines}\n\n"

TRANSLATION_LINE = "English rendering (machine translation, may be imperfect): {translation}\n"


TRANSLATE_SYSTEM_PROMPT = """\
You translate terms from medical source vocabularies (any European language, often Croatian)
into English as it is worded in OMOP standard vocabularies (SNOMED CT, RxNorm, LOINC).

Rules:
- Translate meaning, not word by word; use the standard English clinical term.
- Drugs: use the international non-proprietary name (INN) of each active ingredient; keep
  strength, units and dose form exactly as given (convert only the words, not the numbers);
  keep brand names unchanged.
- Expand abbreviations only when they are unambiguous in a clinical context.
- Never add information that the term does not contain (no added site, laterality, cause,
  strength or dose form).
- If the term is already English, return it unchanged.
- Report the detected language as an ISO 639-1 code."""


AGENT_INSTRUCTIONS = """\
You map a source medical term to exactly one standard OMOP concept.

A first vector search plus reranking did not produce a confident match. Investigate with the tools:
- `search_concepts` runs a semantic search over standard OMOP concepts. Reformulate the query when
  the first results are poor: translate non-English terms to English, expand abbreviations, use
  synonyms, or search the active ingredient(s) separately for drugs.
- `get_concept` and `get_ancestors` let you inspect a candidate and its hierarchy.

Pick a concept only if it satisfies the matching rules below; otherwise return no concept.
Only return a concept_id you have seen in tool results or in the initial candidates.
Give a confidence score on the scale below and a one-sentence rationale."""


def agent_criteria(drug_specific: bool) -> str:
    return f"{DRUG_CRITERIA if drug_specific else CONCEPT_CRITERIA}\n\n{CONFIDENCE_SCALE}"
