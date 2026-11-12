"""Example plugin: a custom workflow step that translates the source term before retrieval.

Source terms are often local-language (e.g. Croatian) names. This step asks a small Pydantic AI
agent to rewrite the term as English, OMOP-style wording and stores it as the search query;
the original name is kept in ``source_name`` and shown in the trace.

Enable it without touching the core code:

    WORKFLOW_PLUGINS=examples.workflow_plugins.translate_query.nodes
    WORKFLOW_DEFINITIONS_DIR=/app/examples/workflow_plugins/translate_query

and pick the ``example_translated`` pipeline. The core has a built-in ``translate`` step and
``automap_translated`` pipeline; this module only demonstrates the plugin mechanism.
"""

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.llm.agents.base import gateway_model
from app.workflows.context import NodeContext
from app.workflows.registry import node
from app.workflows.state import AutomapState


class NormalizedQuery(BaseModel):
    query: str = Field(description="The term in English, using standard OMOP/medical terminology")
    source_language: str | None = Field(default=None, description="Detected language of the input")


normalizer = Agent(
    output_type=NormalizedQuery,
    instructions=(
        "Rewrite the given medical term as concise English medical terminology suitable for searching "
        "the OMOP standard vocabularies (SNOMED, RxNorm, LOINC). Keep drug strengths, units and dose forms. "
        "Expand abbreviations. If the term is already English, return it unchanged."
    ),
    name="query_normalizer",
)


class TranslateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Only translate when the term is not plain ASCII (cheap heuristic to save LLM calls)
    only_non_ascii: bool = False


@node(
    "translate_query",
    params=TranslateParams,
    description="Translate the source term to English OMOP wording",
)
async def translate_query(state: AutomapState, ctx: NodeContext, params: TranslateParams) -> dict:
    term = state.get("query") or state.get("source_name", "")
    if params.only_non_ascii and term.isascii():
        return {"_summary": "term is ASCII, kept as is"}
    model = ctx.agent_model or gateway_model(ctx.llm_model)
    result = await normalizer.run(term, model=model)
    translated = result.output.query.strip() or term
    return {
        "query": translated,
        "meta": {"original_query": term, "source_language": result.output.source_language},
        "_summary": f"'{term}' → '{translated}'",
    }
