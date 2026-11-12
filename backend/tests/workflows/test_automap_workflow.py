"""The default `automap` pipeline with a mocked provider behind a real Instructor client."""

import json

import instructor
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_function_tool_call import Function

from app.llm.structured import Reranker
from app.workflows.service import initial_state, run_pipeline
from tests.workflows.helpers import FakeSearcher, make_context


def _completion(arguments: dict) -> ChatCompletion:
    return ChatCompletion(
        id="cmpl",
        created=0,
        model="test/chat",
        object="chat.completion",
        choices=[
            Choice(
                index=0,
                finish_reason="tool_calls",
                message=ChatCompletionMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ChatCompletionMessageFunctionToolCall(
                            id="call_1",
                            type="function",
                            function=Function(
                                name="RerankerResponse", arguments=json.dumps(arguments)
                            ),
                        )
                    ],
                ),
            )
        ],
    )


class MockProvider:
    """Stands in for the provider: returns queued tool-call responses and records requests."""

    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return _completion(self.responses.pop(0))


def reranker_with(provider: MockProvider) -> Reranker:
    client = AsyncOpenAI(api_key="test", base_url="http://127.0.0.1:9/v1")
    client.chat.completions.create = provider.create  # type: ignore[method-assign]
    return Reranker("test/chat", client=instructor.from_openai(client, mode=instructor.Mode.TOOLS))


def _state(name="metformin 500 mg tableta", value="A10BA02-001", drug=True, threshold=8):
    return initial_state(
        source_id=1,
        source_value=value,
        source_name=name,
        domains=["Drug"],
        drug_specific=drug,
        threshold=threshold,
    )


async def test_accepts_confident_match_with_atc7_filter():
    provider = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    searcher = FakeSearcher()
    result = await run_pipeline(_state(), make_context(reranker_with(provider), searcher=searcher))

    assert result["outcome"] == "accepted"
    assert result["selected"]["concept_id"] == 1 and result["confidence"] == 9
    assert result["method"] == "auto_drug"
    # Drug mode: k=30 and the ATC7 code from the source value restricts the search
    assert searcher.calls[0][1] == 30 and searcher.calls[0][3] == ("A10BA02",)
    # The drug prompt and a numbered candidate list were sent
    messages = provider.requests[0]["messages"]
    assert "Matching rules for drugs" in messages[0]["content"]
    assert "Confidence scale" in messages[0]["content"]
    # Candidates carry class, domain and vocabulary (the branded/generic rule needs the class)
    assert (
        "0: metformin 500 MG Oral Tablet [Clinical Drug | Drug | RxNorm]" in messages[1]["content"]
    )
    assert messages[1]["content"].startswith("Source term: metformin 500 mg tableta")
    # Deterministic by default
    assert provider.requests[0].get("temperature") == 0
    assert [t["node"] for t in result["trace"]] == [
        "extract_atc7",
        "match_previous",
        "match_code",
        "match_name",
        "retrieve",
        "rerank",
        "gate",
    ]


async def test_rejects_below_threshold():
    provider = MockProvider({"most_similar_item_id": 1, "confidence_score": 6})
    result = await run_pipeline(_state(), make_context(reranker_with(provider)))
    assert result["outcome"] == "rejected"
    assert result["selected"]["concept_id"] == 2


async def test_null_means_no_match():
    provider = MockProvider({"most_similar_item_id": None, "confidence_score": None})
    result = await run_pipeline(_state(), make_context(reranker_with(provider)))
    assert result["outcome"] == "no_match" and result["selected"] is None


async def test_out_of_range_id_is_reasked():
    provider = MockProvider(
        {"most_similar_item_id": 7, "confidence_score": 9},  # only 2 candidates
        {"most_similar_item_id": 1, "confidence_score": 8},
    )
    result = await run_pipeline(_state(), make_context(reranker_with(provider)))
    assert len(provider.requests) == 2
    retry_messages = json.dumps(provider.requests[1]["messages"], default=str)
    assert "must be one of the listed ids" in retry_messages
    assert result["outcome"] == "accepted" and result["selected"]["concept_id"] == 2


async def test_provider_error_is_reported_not_guessed():
    class Failing:
        async def create(self, **kwargs):
            raise RuntimeError("provider down")

    client = AsyncOpenAI(api_key="test", base_url="http://127.0.0.1:9/v1")
    client.chat.completions.create = Failing().create  # type: ignore[method-assign]
    reranker = Reranker("test/chat", client=instructor.from_openai(client))
    result = await run_pipeline(_state(), make_context(reranker))
    # Unlike the Streamlit app, a failure does not silently pick the first candidate
    assert result["outcome"] == "error" and result["selected"] is None


async def test_standard_mode_without_candidates():
    provider = MockProvider()
    result = await run_pipeline(
        _state(name="zzzz unknown", value="X1", drug=False), make_context(reranker_with(provider))
    )
    assert result["outcome"] == "no_candidates" and provider.requests == []


async def test_no_atc7_fallback_by_default():
    """As in the Streamlit app: no candidate in the ATC7 class means no match."""
    provider = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    searcher = FakeSearcher()
    result = await run_pipeline(
        _state(value="Z99ZZ99-1"), make_context(reranker_with(provider), searcher=searcher)
    )
    assert len(searcher.calls) == 1 and searcher.calls[0][3] == ("Z99ZZ99",)
    assert result["outcome"] == "no_candidates" and provider.requests == []


async def test_atc7_fallback_can_be_enabled():
    from app.workflows.nodes.retrieve import RetrieveParams, retrieve

    searcher = FakeSearcher()
    state = {**_state(value="Z99ZZ99-1"), "atc7_codes": ["Z99ZZ99"]}
    update = await retrieve(
        state, make_context(None, searcher=searcher), RetrieveParams(atc7_fallback=True)
    )
    assert len(searcher.calls) == 2 and searcher.calls[1][3] == ()
    assert update["candidates"] and "fell back" in update["_summary"]


async def test_rationale_is_kept():
    provider = MockProvider(
        {
            "rationale": "same ingredient and strength",
            "most_similar_item_id": 0,
            "confidence_score": 9,
        }
    )
    result = await run_pipeline(_state(), make_context(reranker_with(provider)))
    assert result["rationale"] == "same ingredient and strength"


async def test_match_type_and_combined_concepts():
    provider = MockProvider(
        {
            "rationale": "metformin with a second listed product",
            "most_similar_item_id": 0,
            "confidence_score": 9,
            "match_type": "broader",
            "additional_item_ids": [1, 0],
        }
    )
    result = await run_pipeline(_state(), make_context(reranker_with(provider)))
    assert result["match_type"] == "broader"
    # The pick itself is not repeated in the combined list
    assert [c["concept_id"] for c in result["selected_concepts"]] == [1, 2]


async def test_out_of_range_additional_id_is_reasked():
    provider = MockProvider(
        {"most_similar_item_id": 0, "confidence_score": 9, "additional_item_ids": [9]},
        {"most_similar_item_id": 0, "confidence_score": 9},
    )
    result = await run_pipeline(_state(), make_context(reranker_with(provider)))
    assert len(provider.requests) == 2 and result["selected_concepts"] == []


class ExamplesOnly:
    """Stands in for app.services.lexical: no text matches, one reviewed example."""

    def match_previous(self, *args):
        from app.services.lexical import TextMatch

        return TextMatch()

    match_code = match_name = match_previous

    def keyword_search(self, *args):
        return []

    def similar_examples(self, term, exclude, limit):
        return [
            {
                "source_name": "metformin 850 mg tbl",
                "mapped_to": "metformin 850 MG Oral Tablet [Clinical Drug]",
            }
        ]


async def test_examples_reach_the_prompt():
    provider = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    await run_pipeline(_state(), make_context(reranker_with(provider), lexical=ExamplesOnly()))
    user = provider.requests[0]["messages"][1]["content"]
    assert user.startswith(
        "Reviewed mappings of similar source terms:\n- metformin 850 mg tbl -> metformin 850 MG"
    )


async def test_rerank_answers_are_cached(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_cache_ttl", 60)
    provider = MockProvider(
        {"most_similar_item_id": 0, "confidence_score": 9, "rationale": "cached-run"}
    )
    first = await run_pipeline(
        _state(name="metformin 500 mg cache test"), make_context(reranker_with(provider))
    )
    again = MockProvider({"most_similar_item_id": 1, "confidence_score": 2})
    second = await run_pipeline(
        _state(name="metformin 500 mg cache test"), make_context(reranker_with(again))
    )
    assert again.requests == [] and second["rationale"] == first["rationale"] == "cached-run"
