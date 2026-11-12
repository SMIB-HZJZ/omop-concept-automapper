"""The built-in `translate` step and the `automap_translated` pipeline."""

from app.llm.structured import RerankResult, TranslationResult
from app.workflows.service import initial_state, run_pipeline
from tests.workflows.helpers import FakeSearcher, make_context


class FakeTranslator:
    def __init__(self, mapping: dict[str, str], fail: bool = False):
        self.mapping = mapping
        self.fail = fail
        self.calls: list[str] = []

    async def translate(self, term):
        self.calls.append(term)
        if self.fail:
            raise RuntimeError("provider down")
        return TranslationResult(self.mapping.get(term, term), "hr")


class RecordingReranker:
    def __init__(self):
        self.kwargs = []

    async def rerank(self, term, candidates, drug_specific, **kwargs):
        self.kwargs.append(kwargs)
        return RerankResult(0, 9)


def _state(name, value="X1", drug=False):
    return initial_state(
        source_id=1,
        source_value=value,
        source_name=name,
        domains=["Condition", "Drug"],
        drug_specific=drug,
        threshold=8,
    )


async def test_translation_searches_both_terms_and_informs_the_reranker():
    searcher = FakeSearcher()
    reranker = RecordingReranker()
    translator = FakeTranslator({"šećerna bolest tip 2": "type 2 diabetes mellitus"})
    ctx = make_context(reranker, searcher=searcher, translator=translator)

    result = await run_pipeline(_state("šećerna bolest tip 2"), ctx, "automap_translated")

    # Original and English term are both searched; the English one finds the concept
    assert [c[0] for c in searcher.calls] == ["šećerna bolest tip 2", "type 2 diabetes mellitus"]
    assert result["selected"]["concept_id"] == 201
    assert result["translated_query"] == "type 2 diabetes mellitus"
    assert reranker.kwargs == [{"translation": "type 2 diabetes mellitus"}]
    assert [t["node"] for t in result["trace"]][:6] == [
        "extract_atc7",
        "match_previous",
        "match_code",
        "translate",
        "match_name",
        "retrieve",
    ]


async def test_english_term_is_not_searched_twice():
    searcher = FakeSearcher()
    ctx = make_context(RecordingReranker(), searcher=searcher, translator=FakeTranslator({}))
    result = await run_pipeline(_state("Type 2 diabetes mellitus"), ctx, "automap_translated")
    assert len(searcher.calls) == 1 and result["translated_query"] is None


async def test_failed_translation_keeps_the_original_term():
    searcher = FakeSearcher()
    ctx = make_context(
        RecordingReranker(), searcher=searcher, translator=FakeTranslator({}, fail=True)
    )
    result = await run_pipeline(_state("type 2 diabetes"), ctx, "automap_translated")
    assert result["outcome"] == "accepted" and len(searcher.calls) == 1


async def test_translations_are_cached(monkeypatch):
    from app.core.redis import get_redis
    from app.llm import structured

    calls = {"n": 0}

    class FakeCompletions:
        async def create(self, **kwargs):
            calls["n"] += 1
            return structured.TranslationResponse(english="headache", language="hr")

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    translator = structured.Translator("test/cache-model", client=FakeClient())
    get_redis().delete(translator._key("glavobolja"))
    first = await translator.translate("glavobolja")
    second = await translator.translate("glavobolja")
    assert calls["n"] == 1
    assert (first.english, first.cached, second.english, second.cached) == (
        "headache",
        False,
        "headache",
        True,
    )
