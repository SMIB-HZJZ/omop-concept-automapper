"""The default `automap` pipeline with a mocked provider behind a real Instructor client."""

import json

import instructor
import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage, ChatCompletionMessageFunctionToolCall
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
                            function=Function(name="RerankerResponse", arguments=json.dumps(arguments)),
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
        source_id=1, source_value=value, source_name=name, domains=["Drug"], drug_specific=drug, threshold=threshold
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
    assert "Match the given drug name" in messages[0]["content"]
    assert "0: metformin 500 MG Oral Tablet" in messages[1]["content"]
    assert [t["node"] for t in result["trace"]] == ["extract_atc7", "retrieve", "rerank", "gate"]


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


@pytest.mark.parametrize("fallback", [True])
async def test_atc7_fallback_when_filtered_search_is_empty(fallback):
    provider = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    searcher = FakeSearcher()
    # Source claims an ATC7 code no candidate has; retrieval retries without the filter
    result = await run_pipeline(
        _state(value="Z99ZZ99-1"), make_context(reranker_with(provider), searcher=searcher)
    )
    assert len(searcher.calls) == 2 and searcher.calls[1][3] == ()
    assert result["outcome"] == "accepted"
