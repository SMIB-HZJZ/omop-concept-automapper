"""Rerank cascade: voters that agree decide; otherwise the judge decides, seeing their picks."""

import instructor
from openai import AsyncOpenAI

from app.llm.gateway import chat_model_settings
from app.llm.structured import (
    CascadeReranker,
    EscalatingReranker,
    Reranker,
    instructor_mode_for,
)
from app.workflows.service import initial_state, run_pipeline
from tests.workflows.helpers import make_context
from tests.workflows.test_automap_workflow import MockProvider

CANDIDATES = [
    {"concept_id": 1, "concept_name": "metformin 500 MG Oral Tablet"},
    {"concept_id": 2, "concept_name": "metformin 850 MG Oral Tablet"},
]


class FailingProvider:
    async def create(self, **kwargs):
        raise RuntimeError("provider down")


def _reranker(model: str, provider) -> Reranker:
    client = AsyncOpenAI(api_key="test", base_url="http://127.0.0.1:9/v1")
    client.chat.completions.create = provider.create  # type: ignore[method-assign]
    # Distinct model names: the rerank cache is keyed by model and prompt
    return Reranker(model, client=instructor.from_openai(client, mode=instructor.Mode.TOOLS))


def _user_prompt(provider: MockProvider) -> str:
    return next(m["content"] for m in provider.requests[0]["messages"] if m["role"] == "user")


async def test_agreeing_voters_decide_without_the_judge():
    a = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    b = MockProvider({"most_similar_item_id": 0, "confidence_score": 7})
    judge = MockProvider()
    cascade = CascadeReranker(
        [_reranker("test/cascade-a1", a), _reranker("test/cascade-b1", b)],
        _reranker("test/cascade-j1", judge),
    )
    result = await cascade.rerank("metformin 500 mg tbl", CANDIDATES, drug_specific=True)
    assert (result.index, result.confidence, result.route) == (0, 7, "agreed")
    assert [v["index"] for v in result.votes] == [0, 0]
    assert judge.requests == []


async def test_disagreement_goes_to_the_judge_with_the_votes():
    a = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    b = MockProvider({"most_similar_item_id": 1, "confidence_score": 6})
    judge = MockProvider({"most_similar_item_id": 1, "confidence_score": 8})
    cascade = CascadeReranker(
        [_reranker("test/cascade-a2", a), _reranker("test/cascade-b2", b)],
        _reranker("test/cascade-j2", judge),
    )
    result = await cascade.rerank("metformin 850 mg tbl", CANDIDATES, drug_specific=True)
    assert (result.index, result.confidence, result.route) == (1, 8, "judge")
    prompt = _user_prompt(judge)
    assert "Reviewer 1: 0: metformin 500 MG Oral Tablet (confidence 9)" in prompt
    assert "Reviewer 2: 1: metformin 850 MG Oral Tablet (confidence 6)" in prompt
    # The voters themselves never see other votes
    assert "Reviewer" not in _user_prompt(a)


async def test_failed_voter_counts_as_disagreement():
    a = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    judge = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    cascade = CascadeReranker(
        [_reranker("test/cascade-a3", a), _reranker("test/cascade-b3", FailingProvider())],
        _reranker("test/cascade-j3", judge),
    )
    result = await cascade.rerank("metformin 500 mg tbl", CANDIDATES, drug_specific=True)
    assert result.route == "judge"
    assert "Reviewer 2: no answer (error)" in _user_prompt(judge)


async def test_pipeline_marks_judge_decisions_in_the_rationale():
    a = MockProvider({"most_similar_item_id": 0, "confidence_score": 9, "rationale": "a"})
    b = MockProvider({"most_similar_item_id": 1, "confidence_score": 9, "rationale": "b"})
    judge = MockProvider({"most_similar_item_id": 0, "confidence_score": 9, "rationale": "500 mg"})
    cascade = CascadeReranker(
        [_reranker("test/cascade-a4", a), _reranker("test/cascade-b4", b)],
        _reranker("test/cascade-j4", judge),
    )
    state = initial_state(
        source_id=1,
        source_value="X-1",
        source_name="metformin 500 mg tableta",
        domains=["Drug"],
        drug_specific=True,
        threshold=8,
    )
    result = await run_pipeline(state, make_context(cascade))
    assert result["rationale"].startswith("[voters disagreed, judge decided]")
    assert result["meta"]["rerank_route"] == "judge"


def test_instructor_mode_override_by_model_prefix():
    # Default override: Claude via OpenRouter rejects forced tool choice, so it uses JSON schema
    assert (
        instructor_mode_for("openrouter/anthropic/claude-sonnet-5.5") == instructor.Mode.JSON_SCHEMA
    )
    assert instructor_mode_for("openrouter/openai/gpt-oss-120b") == instructor.Mode.TOOLS
    assert instructor_mode_for("anthropic/claude-sonnet-5-5") == instructor.Mode.TOOLS
    assert instructor_mode_for(None) == instructor.Mode.TOOLS


async def test_confident_first_answer_is_not_escalated():
    first = MockProvider({"most_similar_item_id": 0, "confidence_score": 9})
    strong = MockProvider()
    reranker = EscalatingReranker(
        _reranker("test/esc-f1", first), _reranker("test/esc-s1", strong), below=8
    )
    result = await reranker.rerank("metformin 500 mg tbl", CANDIDATES, drug_specific=True)
    assert (result.index, result.confidence, result.route) == (0, 9, "first")
    assert strong.requests == []


async def test_unsure_answer_is_escalated_with_the_same_prompt():
    first = MockProvider({"most_similar_item_id": 0, "confidence_score": 6})
    strong = MockProvider({"most_similar_item_id": 1, "confidence_score": 9})
    reranker = EscalatingReranker(
        _reranker("test/esc-f2", first), _reranker("test/esc-s2", strong), below=8
    )
    result = await reranker.rerank("metformin 850 mg tbl", CANDIDATES, drug_specific=True)
    assert (result.index, result.confidence, result.route) == (1, 9, "escalated")
    assert result.votes == [{"model": "test/esc-f2", "index": 0, "confidence": 6}]
    assert _user_prompt(strong) == _user_prompt(first)


async def test_failed_first_answer_is_escalated_and_failed_escalation_keeps_the_first():
    strong = MockProvider({"most_similar_item_id": 1, "confidence_score": 8})
    reranker = EscalatingReranker(
        _reranker("test/esc-f3", FailingProvider()), _reranker("test/esc-s3", strong), below=8
    )
    result = await reranker.rerank("metformin 850 mg tbl", CANDIDATES, drug_specific=True)
    assert (result.index, result.route) == (1, "escalated")

    first = MockProvider({"most_similar_item_id": 0, "confidence_score": 5})
    reranker = EscalatingReranker(
        _reranker("test/esc-f4", first), _reranker("test/esc-s4", FailingProvider()), below=8
    )
    result = await reranker.rerank("metformin 500 mg tbl", CANDIDATES, drug_specific=True)
    assert (result.index, result.confidence, result.route) == (0, 5, "first")


def test_no_temperature_for_models_that_reject_sampling():
    assert chat_model_settings("anthropic/claude-fable-5-2") == {}
    # Claude 5.x returns a 400 for a non-default temperature
    assert chat_model_settings("anthropic/claude-sonnet-5-5") == {}
    assert chat_model_settings("openrouter/anthropic/claude-opus-5.5") == {}
    assert chat_model_settings("openai/gpt-6.1-terra") == {"temperature": 0.0}


def test_effort_is_sent_as_reasoning_effort():
    assert chat_model_settings("anthropic/claude-sonnet-5-5", "low") == {"reasoning_effort": "low"}
    assert chat_model_settings("openai/gpt-6.1-terra", "high") == {
        "temperature": 0.0,
        "reasoning_effort": "high",
    }
    assert chat_model_settings("openai/gpt-6.1-terra", "") == {"temperature": 0.0}


def _effort_reranker(model: str, provider, effort: str | None) -> Reranker:
    client = AsyncOpenAI(api_key="test", base_url="http://127.0.0.1:9/v1")
    client.chat.completions.create = provider.create  # type: ignore[method-assign]
    return Reranker(
        model, client=instructor.from_openai(client, mode=instructor.Mode.TOOLS), effort=effort
    )


async def test_reranker_sends_its_effort_and_caches_per_effort():
    low = MockProvider({"most_similar_item_id": 0, "confidence_score": 6})
    high = MockProvider({"most_similar_item_id": 1, "confidence_score": 9})
    # Same model and prompt, different effort: separate answers (no cache hit across efforts)
    a = await _effort_reranker("test/effort-1", low, "low").rerank(
        "metformin 850 mg tbl", CANDIDATES, drug_specific=True
    )
    b = await _effort_reranker("test/effort-1", high, "high").rerank(
        "metformin 850 mg tbl", CANDIDATES, drug_specific=True
    )
    assert (a.index, b.index) == (0, 1)
    assert low.requests[0]["reasoning_effort"] == "low"
    assert high.requests[0]["reasoning_effort"] == "high"


async def test_escalation_to_the_same_model_with_more_effort():
    first = MockProvider({"most_similar_item_id": 0, "confidence_score": 5})
    again = MockProvider({"most_similar_item_id": 1, "confidence_score": 9})
    reranker = EscalatingReranker(
        _effort_reranker("test/effort-2", first, None),
        _effort_reranker("test/effort-2", again, "xhigh"),
        below=8,
    )
    result = await reranker.rerank("metformin 850 mg tbl", CANDIDATES, drug_specific=True)
    assert (result.index, result.route) == (1, "escalated")
    assert "reasoning_effort" not in first.requests[0]
    assert again.requests[0]["reasoning_effort"] == "xhigh"
    assert reranker.label == "test/effort-2 → test/effort-2 (xhigh effort) (confidence < 8)"


def test_build_reranker_effort_settings():
    from app.workflows import service

    settings = service.get_settings()
    saved = (
        settings.llm_reasoning_effort,
        settings.rerank_escalate_model,
        settings.rerank_escalate_effort,
        settings.rerank_voters,
    )
    try:
        settings.rerank_voters = ""
        settings.llm_reasoning_effort = "medium"
        settings.rerank_escalate_model = "test/chat"
        settings.rerank_escalate_effort = "medium"
        # Same model, same effort: escalation would only repeat the answer
        assert isinstance(service.build_reranker("test/chat"), Reranker)
        settings.rerank_escalate_effort = "max"
        built = service.build_reranker("test/chat")
        assert isinstance(built, EscalatingReranker)
        assert (built.first.effort, built.stronger.effort) == ("medium", "max")
    finally:
        (
            settings.llm_reasoning_effort,
            settings.rerank_escalate_model,
            settings.rerank_escalate_effort,
            settings.rerank_voters,
        ) = saved
