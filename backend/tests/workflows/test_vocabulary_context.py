"""The source vocabulary's name and description reach the LLM steps."""

from app.llm import structured
from app.llm.prompts import vocabulary_context
from app.workflows.service import initial_state, run_pipeline
from tests.workflows.helpers import FakeSearcher, make_context
from tests.workflows.test_translate import RecordingReranker


def test_vocabulary_context_text():
    assert vocabulary_context("Lab orders", " Tests ordered,\n not their results. ") == (
        "Lab orders - Tests ordered, not their results."
    )
    assert vocabulary_context("Lab orders", None) == "Lab orders"
    # The generated default name says nothing
    assert vocabulary_context("Vocabulary 3", None) is None
    assert vocabulary_context("Vocabulary 3", "Discharge diagnoses") == "Discharge diagnoses"


async def test_reranker_gets_the_vocabulary_context():
    reranker = RecordingReranker()
    state = initial_state(
        source_id=1,
        source_value="X1",
        source_name="type 2 diabetes",
        domains=["Condition"],
        drug_specific=False,
        threshold=8,
        vocabulary_context="Lab orders - tests ordered, not their results",
    )
    await run_pipeline(state, make_context(reranker, searcher=FakeSearcher()), "automap")
    assert reranker.kwargs[0]["context"] == "Lab orders - tests ordered, not their results"


class _Capture:
    def __init__(self, response):
        self.messages = []
        self.response = response
        outer = self

        class Completions:
            async def create(self, **kwargs):
                outer.messages.append(kwargs["messages"])
                return outer.response

        self.chat = type("Chat", (), {"completions": Completions()})()


async def test_context_is_in_the_prompt_and_the_cache_key(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_cache_ttl", 0)
    client = _Capture(
        structured.RerankerResponse(
            most_similar_item_id=0, confidence_score=9, rationale="same test"
        )
    )
    reranker = structured.Reranker("test/ctx-model", client=client)
    candidates = [{"concept_id": 1, "concept_name": "Hemoglobin A1c measurement"}]
    await reranker.rerank("HbA1c", candidates, False, context="Lab orders - tests ordered")
    await reranker.rerank("HbA1c", candidates, False)
    with_ctx, without = (m[1]["content"] for m in client.messages)
    assert (
        "Source vocabulary (applies to every term in it): Lab orders - tests ordered\n" in with_ctx
    )
    assert "Source vocabulary" not in without

    translator = structured.Translator("test/ctx-model")
    assert translator._key("HbA1c") != translator._key("HbA1c", "Lab orders")
    assert translator._key("HbA1c") == translator._key("HbA1c", None)
