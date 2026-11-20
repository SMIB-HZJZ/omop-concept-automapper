"""The refine stage of every pipeline: a Pydantic AI agent retries unsure concepts with tools."""

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.llm.structured import RerankResult
from app.workflows.service import initial_state, run_pipeline
from tests.workflows.helpers import FakeSearcher, make_context


class LowConfidenceReranker:
    async def rerank(self, term, candidates, drug_specific):
        return RerankResult(0, 3)


def scripted_agent(final_args: dict):
    """First turn: search with a reformulated (translated) query. Second turn: final answer."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_returns = [
            p for m in messages for p in getattr(m, "parts", []) if p.part_kind == "tool-return"
        ]
        if not tool_returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_concepts", {"query": "acetaminophen 500 mg tablet", "k": 5}
                    )
                ]
            )
        output_tool = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(output_tool, final_args)])

    return FunctionModel(respond)


def _state():
    return initial_state(
        source_id=5,
        source_value="N02BE01-XYZ",
        source_name="paracetamol 500 mg tablete",
        domains=["Drug"],
        drug_specific=False,
        threshold=8,
    )


async def test_agent_refines_rejected_mapping():
    searcher = FakeSearcher()
    ctx = make_context(
        LowConfidenceReranker(),
        searcher=searcher,
        agent_model=scripted_agent(
            {"concept_id": 3, "confidence": 9, "rationale": "paracetamol = acetaminophen"}
        ),
    )
    result = await run_pipeline(_state(), ctx, "automap")

    assert result["outcome"] == "accepted"
    assert result["selected"]["concept_id"] == 3
    assert result["method"] == "auto_agentic"
    assert result["rationale"] == "paracetamol = acetaminophen"
    assert ("acetaminophen 500 mg tablet", 5, ("Drug",), ()) in searcher.calls
    assert [t["node"] for t in result["trace"]] == [
        "extract_atc7",
        "match_previous",
        "match_code",
        "translate",
        "match_name",
        "retrieve",
        "rerank",
        "gate",
        "rewrite",
        "refine",
        "final_gate",
    ]


async def test_agent_cannot_invent_concepts():
    """An id the agent never saw triggers a retry; a model that insists ends in an error, not a mapping."""
    ctx = make_context(
        LowConfidenceReranker(),
        agent_model=scripted_agent(
            {"concept_id": 424242, "confidence": 10, "rationale": "made up"}
        ),
    )
    result = await run_pipeline(_state(), ctx, "automap")
    assert result["outcome"] in ("error", "rejected")
    assert result.get("selected") is None or result["selected"]["concept_id"] != 424242


async def test_a_less_sure_or_failing_agent_keeps_the_first_pick():
    def answer(confidence):
        def respond(messages, info):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {"concept_id": 1, "confidence": confidence, "rationale": "agent"},
                    )
                ]
            )

        return FunctionModel(respond)

    def first_pick(result):
        return result["selected"]["concept_id"], result["confidence"], result.get("method")

    # The reranker's pick (candidate 0, confidence 3) stays when the agent is no surer
    kept = await run_pipeline(
        _state(), make_context(LowConfidenceReranker(), agent_model=answer(3)), "automap"
    )
    assert kept["confidence"] == 3 and kept.get("method") != "auto_agentic"
    assert kept["outcome"] == "rejected"

    async def broken(*args, **kwargs):
        raise RuntimeError("gateway down")

    failed = await run_pipeline(
        _state(), make_context(LowConfidenceReranker(), run_agent=broken), "automap"
    )
    assert first_pick(failed) == first_pick(kept) and failed["outcome"] == "rejected"


async def test_agent_sees_the_translation():
    seen = {}

    async def runner(model, term, deps, candidates, translation=None):
        from app.llm.schemas import MappingDecision

        seen["translation"] = translation
        return MappingDecision(concept_id=None, rationale="-")

    from tests.workflows.test_translate import FakeTranslator

    ctx = make_context(
        LowConfidenceReranker(),
        translator=FakeTranslator({"paracetamol 500 mg tablete": "paracetamol 500 mg tablet"}),
        run_agent=runner,
    )
    await run_pipeline(_state(), ctx, "automap")
    assert seen["translation"] == "paracetamol 500 mg tablet"


async def test_agent_no_match():
    def respond(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name, {"concept_id": None, "rationale": "nothing fits"}
                )
            ]
        )

    class NoPickReranker:
        async def rerank(self, term, candidates, drug_specific, **kwargs):
            return RerankResult(None, None)

    ctx = make_context(NoPickReranker(), agent_model=FunctionModel(respond))
    result = await run_pipeline(_state(), ctx, "automap")
    assert result["outcome"] == "no_match" and result["rationale"] == "nothing fits"


def test_text_part_import_kept():
    # TextPart is part of the public message API used by custom FunctionModels
    assert TextPart("x").content == "x"


async def test_agent_stops_at_the_request_limit(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "agent_request_limit", 3)
    calls = []

    def endless(messages, info):
        calls.append(1)
        return ModelResponse(
            parts=[ToolCallPart("search_concepts", {"query": f"try {len(calls)}"})]
        )

    ctx = make_context(LowConfidenceReranker(), agent_model=FunctionModel(endless))
    result = await run_pipeline(_state(), ctx, "automap")
    assert len(calls) == 3
    # The first pick stands
    assert result["confidence"] == 3 and result.get("method") != "auto_agentic"
    refine = next(t for t in result["trace"] if t["node"] == "refine")
    assert "UsageLimitExceeded" in refine["summary"]


async def test_rewritten_search_gives_the_reranker_new_candidates():
    class Rewriter:
        async def rewrite(self, term, **kwargs):
            self.shown = kwargs.get("shown")
            return ["acetaminophen 500 mg tablet"]

    class SecondTimeSure:
        """Unsure about the first candidates; sure once the rewritten search added concept 3."""

        def __init__(self):
            self.calls = 0

        async def rerank(self, term, candidates, drug_specific, **kwargs):
            self.calls += 1
            ids = [c["concept_id"] for c in candidates]
            if 3 in ids and self.calls == 2:
                return RerankResult(ids.index(3), 9)
            return RerankResult(0, 3)

    class BySearch(FakeSearcher):
        """The original term finds only metformin; the rewritten one finds acetaminophen."""

        def search(self, query, k, filters):
            from tests.workflows.helpers import CONCEPTS

            self.calls.append((query, k, tuple(filters.domains), tuple(filters.atc7_codes)))
            wanted = 3 if "acetaminophen" in query else 1
            return [{**CONCEPTS[wanted], "score": 0.5, "rank": 1}]

    searcher = BySearch()
    rewriter = Rewriter()
    reranker = SecondTimeSure()
    ctx = make_context(reranker, searcher=searcher, rewriter=rewriter)
    result = await run_pipeline(_state(), ctx, "automap")
    assert result["outcome"] == "accepted" and result["selected"]["concept_id"] == 3
    assert result["rewritten_queries"] == ["acetaminophen 500 mg tablet"]
    assert reranker.calls == 2 and rewriter.shown
    nodes = [t["node"] for t in result["trace"]]
    assert nodes[-4:] == ["gate", "rewrite", "rerank_again", "gate_again"]


async def test_a_less_sure_second_pick_keeps_the_first():
    class Rewriter:
        async def rewrite(self, term, **kwargs):
            return ["acetaminophen 500 mg tablet"]

    class LessSure:
        def __init__(self):
            self.calls = 0

        async def rerank(self, term, candidates, drug_specific, **kwargs):
            self.calls += 1
            return RerankResult(0, 5) if self.calls == 1 else RerankResult(len(candidates) - 1, 2)

    ctx = make_context(LessSure(), rewriter=Rewriter())
    result = await run_pipeline(_state(), ctx, "automap")
    assert result["confidence"] == 5 and result["outcome"] == "rejected"
