from pydantic import BaseModel, ConfigDict

from app.llm.structured import Translator
from app.workflows.context import NodeContext
from app.workflows.registry import node
from app.workflows.state import AutomapState


class TranslateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Search with the English rendering only (False: retrieval also searches the original term)
    replace_query: bool = False


@node(
    "translate",
    params=TranslateParams,
    description="Render the source term in English OMOP wording before retrieval (cached)",
)
async def translate(state: AutomapState, ctx: NodeContext, params: TranslateParams) -> dict:
    term = state.get("query") or state.get("source_name", "")
    translator = ctx.translator or Translator(
        ctx.llm_model, effort=ctx.settings.llm_reasoning_effort
    )
    try:
        result = await translator.translate(term)
    except Exception as exc:
        # A failed translation must not lose the concept: continue with the original term
        ctx.logger.warning("Translation failed for %r: %s", term, exc)
        return {"_summary": f"translation failed ({type(exc).__name__}), kept original"}
    english = result.english or term
    same = english.strip().lower() == term.strip().lower()
    update: dict = {
        "translated_query": None if same else english,
        "meta": {"source_language": result.language, "translation_cached": result.cached},
        "_summary": "already English" if same else f"'{term}' → '{english}' ({result.language})",
    }
    if params.replace_query and not same:
        update["query"] = english
    return update
