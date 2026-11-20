"""The stronger model re-checks only unsure-but-plausible picks; how a result came about is kept."""

from app.llm.structured import EscalatingReranker, RerankResult
from app.workflows.steps import decision_path


class Fixed:
    def __init__(self, result=None, fail=False):
        self.result, self.fail, self.calls = result, fail, 0
        self.model = self.label = "test/fixed"

    async def rerank(self, term, candidates, drug_specific, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return self.result


CANDIDATES = [{"concept_id": 1, "concept_name": "a"}, {"concept_id": 2, "concept_name": "b"}]


async def _escalates(first_result=None, fail=False) -> bool:
    stronger = Fixed(RerankResult(1, 9))
    reranker = EscalatingReranker(Fixed(first_result, fail), stronger, below=8, from_confidence=5)
    await reranker.rerank("t", CANDIDATES, False)
    return stronger.calls == 1


async def test_only_plausible_unsure_picks_are_re_checked():
    assert await _escalates(RerankResult(0, 6))
    assert await _escalates(RerankResult(0, 5))
    assert not await _escalates(RerankResult(0, 4))  # too unsure: a new search helps more
    assert not await _escalates(RerankResult(None, None))  # no pick: the same
    assert not await _escalates(RerankResult(0, 8))  # sure enough
    assert await _escalates(fail=True)  # a failed call is still re-done


def test_decision_path():
    concept = {"concept_id": 1, "concept_name": "x"}
    state = {
        "attempts": [
            {"step": "AI pick", "concept": concept, "confidence": 6, "route": "escalated"},
            {"step": "second AI pick, after rewritten searches", "concept": None},
            {"step": "agent retry", "concept": concept, "confidence": 7},
            {"step": "final decision", "concept": concept, "confidence": 8},
        ]
    }
    assert decision_path(state) == (
        "AI pick (stronger model) 6 → AI pick after rewrite no match → agent retry 7 → "
        "final decision 8"
    )
    assert decision_path({}) is None
