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
    language = state.get("source_language")
    if language == "en":
        # Set on the vocabulary: nothing to translate, no LLM call
        return {"meta": {"source_language": "en"}, "_summary": "English vocabulary, skipped"}
    try:
        context = state.get("vocabulary_context")
        kwargs = {"language": language} if language else {}
        if state.get("source_description"):
            kwargs["description"] = state["source_description"]
        result = await (
            translator.translate(term, context, **kwargs)
            if context
            else translator.translate(term, **kwargs)
        )
    except Exception as exc:
        # A failed translation must not lose the concept: continue with the original term
        ctx.logger.warning("Translation failed for %r: %s", term, exc)
        return {"_summary": f"translation failed ({type(exc).__name__}), kept original"}
    english = result.english or term
    # What the model detected for this term; a vocabulary set to "hr" may still hold Latin terms
    detected = (result.language or "").lower() or language
    label = detected or "?"
    meta: dict = {"source_language": detected, "translation_cached": result.cached}
    if language:
        meta["vocabulary_language"] = language
        if detected != language:
            label = f"{detected or '?'}, vocabulary: {language}"
    same = english.strip().lower() == term.strip().lower()
    update: dict = {
        "translated_query": None if same else english,
        "meta": meta,
        "_summary": "already English" if same else f"'{term}' → '{english}' ({label})",
    }
    if params.replace_query and not same:
        update["query"] = english
    return update
