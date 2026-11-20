"""The last step: the stronger model decides on every candidate and verdict gathered."""

from app.llm.structured import RerankResult
from app.workflows.nodes.decide import all_candidates, evidence_notes
from app.workflows.service import initial_state, run_pipeline
from tests.workflows.helpers import CONCEPTS, FakeSearcher, make_context


class Unsure:
    async def rerank(self, term, candidates, drug_specific, **kwargs):
        return RerankResult(0, 4, rationale="first guess")


class Judge:
    """Picks acetaminophen from the combined list and records what it was told."""

    def __init__(self, pick=3, confidence=8):
        self.pick, self.confidence = pick, confidence

    async def rerank(self, term, candidates, drug_specific, **kwargs):
        self.candidates = [c["concept_id"] for c in candidates]
        self.notes = kwargs.get("notes") or []
        if self.pick is None:
            return RerankResult(None, None)
        return RerankResult(self.candidates.index(self.pick), self.confidence, rationale="all")


def _state():
    return initial_state(
        source_id=5,
        source_value="X",
        source_name="paracetamol 500 mg tablete",
        domains=["Drug"],
        drug_specific=False,
        threshold=8,
    )


async def test_final_decision_sees_all_evidence_and_can_settle_the_term():
    async def agent(model, term, deps, candidates, translation=None):
        from app.llm.schemas import MappingDecision

        deps.seen[2] = CONCEPTS[2]  # something only the agent found
        return MappingDecision(concept_id=2, confidence=5, rationale="agent thinks 850 mg")

    judge = Judge()
    ctx = make_context(
        Unsure(), searcher=FakeSearcher(), run_agent=agent, extras={"final_reranker": judge}
    )
    result = await run_pipeline(_state(), ctx, "automap")

    assert result["outcome"] == "accepted" and result["selected"]["concept_id"] == 3
    assert result["method"] == "auto_final"
    assert result["rationale"].startswith("[final decision on all evidence]")
    # Earlier verdicts reach the judge; earlier picks come first in its list
    text = "\n".join(judge.notes)
    assert "AI pick:" in text and "confidence 4" in text and "first guess" in text
    assert "agent retry: metformin 850 MG Oral Tablet (ID 2) [confidence 5]" in text
    assert judge.candidates[:2] == [result["attempts"][0]["concept"]["concept_id"], 2]
    assert [t["node"] for t in result["trace"]][-3:] == ["final_gate", "decide", "last_gate"]


async def test_no_final_pick_keeps_the_earlier_result():
    ctx = make_context(Unsure(), extras={"final_reranker": Judge(pick=None)})
    result = await run_pipeline(_state(), ctx, "automap")
    assert result["confidence"] == 4 and result["outcome"] == "rejected"
    assert result.get("method") != "auto_final"


async def test_a_failing_final_decision_keeps_the_earlier_result():
    class Broken:
        async def rerank(self, *args, **kwargs):
            raise RuntimeError("provider down")

    ctx = make_context(Unsure(), extras={"final_reranker": Broken()})
    result = await run_pipeline(_state(), ctx, "automap")
    assert result["confidence"] == 4 and result["outcome"] == "rejected"


def test_evidence_helpers():
    state = {
        "attempts": [
            {
                "step": "AI pick",
                "concept": CONCEPTS[1],
                "confidence": 6,
                "match_type": "broader",
                "rationale": "close",
                "route": "escalated",
            },
            {"step": "agent retry", "concept": None, "confidence": None, "rationale": "nothing"},
        ],
        "rewritten_queries": ["acetaminophen tablet"],
        "candidates": [CONCEPTS[2], CONCEPTS[1]],
        "agent_candidates": [CONCEPTS[3]],
    }
    notes = evidence_notes(state)
    assert any("broader match, decided by the stronger model" in n for n in notes)
    assert any("agent retry: no suitable concept: nothing" in n for n in notes)
    assert any("'acetaminophen tablet'" in n for n in notes)
    assert [c["concept_id"] for c in all_candidates(state, 10)] == [1, 2, 3]
    assert len(all_candidates(state, 2)) == 2


async def test_second_pick_has_context_and_no_escalation_final_decision_has_context():
    from app.llm.structured import EscalatingReranker

    class Recorder:
        def __init__(self, confidence):
            self.confidence = confidence
            self.seen = []
            self.model = self.label = "test/recorder"

        async def rerank(self, term, candidates, drug_specific, **kwargs):
            self.seen.append([bool(c.get("context")) for c in candidates])
            return RerankResult(0, self.confidence)

    class Rewriter:
        async def rewrite(self, term, **kwargs):
            return ["acetaminophen"]

    class BySearch(FakeSearcher):
        def search(self, query, k, filters):
            self.calls.append(query)
            wanted = 3 if "acetaminophen" in query else 1
            return [{**CONCEPTS[wanted], "score": 0.5, "rank": 1}]

    first, stronger, judge = Recorder(4), Recorder(5), Judge(pick=3, confidence=6)
    ctx = make_context(
        EscalatingReranker(first, stronger, 8),
        searcher=BySearch(),
        rewriter=Rewriter(),
        get_concept_context=lambda ids: {i: {"parents": ["p"]} for i in ids},
        extras={"final_reranker": judge},
    )
    await run_pipeline(_state(), ctx, "automap")
    # First pick: names only, escalated; second pick: first model only, with context
    assert first.seen == [[False], [True, True]]
    assert stronger.seen == [[False]]
    assert judge.candidates and judge.notes
