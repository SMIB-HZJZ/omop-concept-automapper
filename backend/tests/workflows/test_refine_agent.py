"""The `automap_refine` pipeline: a Pydantic AI agent retries rejected concepts with tools."""

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
    result = await run_pipeline(_state(), ctx, "automap_refine")

    assert result["outcome"] == "accepted"
    assert result["selected"]["concept_id"] == 3
    assert result["method"] == "auto_agentic"
    assert result["rationale"] == "paracetamol = acetaminophen"
    assert ("acetaminophen 500 mg tablet", 5, ("Drug",), ()) in searcher.calls
    assert [t["node"] for t in result["trace"]] == [
        "extract_atc7",
        "retrieve",
        "rerank",
        "gate",
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
    result = await run_pipeline(_state(), ctx, "automap_refine")
    assert result["outcome"] in ("error", "rejected")
    assert result.get("selected") is None or result["selected"]["concept_id"] != 424242


async def test_agent_no_match():
    def respond(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name, {"concept_id": None, "rationale": "nothing fits"}
                )
            ]
        )

    ctx = make_context(LowConfidenceReranker(), agent_model=FunctionModel(respond))
    result = await run_pipeline(_state(), ctx, "automap_refine")
    assert result["outcome"] == "no_match" and result["rationale"] == "nothing fits"


def test_text_part_import_kept():
    # TextPart is part of the public message API used by custom FunctionModels
    assert TextPart("x").content == "x"
