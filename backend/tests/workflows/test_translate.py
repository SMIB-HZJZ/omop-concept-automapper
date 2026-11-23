"""The built-in `translate` step, part of the default `automap` pipeline."""

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

    result = await run_pipeline(_state("šećerna bolest tip 2"), ctx, "automap")

    # Original and English term are both searched; the English one finds the concept
    assert [c[0] for c in searcher.calls] == ["šećerna bolest tip 2", "type 2 diabetes mellitus"]
    assert result["selected"]["concept_id"] == 201
    assert result["translated_query"] == "type 2 diabetes mellitus"
    assert reranker.kwargs == [{"translation": "type 2 diabetes mellitus"}]
    assert [t["node"] for t in result["trace"]][:7] == [
        "extract_atc7",
        "match_previous",
        "match_code",
        "match_name",
        "translate",
        "match_english",
        "retrieve",
    ]


async def test_english_term_is_not_searched_twice():
    searcher = FakeSearcher()
    ctx = make_context(RecordingReranker(), searcher=searcher, translator=FakeTranslator({}))
    result = await run_pipeline(_state("Type 2 diabetes mellitus"), ctx, "automap")
    assert len(searcher.calls) == 1 and result["translated_query"] is None


async def test_failed_translation_keeps_the_original_term():
    searcher = FakeSearcher()
    ctx = make_context(
        RecordingReranker(), searcher=searcher, translator=FakeTranslator({}, fail=True)
    )
    result = await run_pipeline(_state("type 2 diabetes"), ctx, "automap")
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


def test_embed_many_splits_into_parallel_requests_in_order(monkeypatch):
    from app.core.config import get_settings
    from app.llm.embeddings import Embedder, request_size

    calls = []

    def embed(self, texts, kind="document"):
        calls.append(list(texts))
        return [[float(t)] for t in texts]

    monkeypatch.setattr(Embedder, "embed", embed)
    monkeypatch.setattr(get_settings(), "embedding_concurrency", 3)
    assert request_size("gemini/gemini-embedding-2") == 100
    assert request_size("openai/text-embedding-3-large") == 1024
    assert request_size("ollama/bge-m3") == 256
    texts = [str(i) for i in range(250)]
    vectors = Embedder("gemini/gemini-embedding-2", 8).embed_many(texts)
    assert [v[0] for v in vectors] == [float(i) for i in range(250)]  # input order kept
    assert sorted(len(c) for c in calls) == [50, 100, 100]

    monkeypatch.setattr(get_settings(), "embedding_request_size", 40)
    assert request_size("gemini/gemini-embedding-2") == 40


class EnglishNameOnly:
    """Stands in for app.services.lexical: only the English term matches a concept exactly."""

    def match_name(self, names, vocabularies=None):
        from app.services.lexical import TextMatch
        from tests.workflows.helpers import CONCEPTS

        if names == ["type 2 diabetes mellitus"]:
            return TextMatch(concepts=[CONCEPTS[201]], via="name")
        return TextMatch()

    def match_previous(self, *args):
        from app.services.lexical import TextMatch

        return TextMatch()

    match_code = match_previous

    def similar_examples(self, *args):
        return []


async def test_an_exact_match_of_the_translation_is_left_to_the_reranker():
    class Recording:
        async def rerank(self, term, candidates, drug_specific, **kwargs):
            self.first = candidates[0]["concept_id"]
            return RerankResult(0, 9)

    reranker = Recording()
    translator = FakeTranslator({"šećerna bolest tip 2": "type 2 diabetes mellitus"})
    ctx = make_context(reranker, translator=translator, lexical=EnglishNameOnly())
    result = await run_pipeline(_state("šećerna bolest tip 2"), ctx, "automap")
    # Not accepted by the name step: the reranker saw it first and chose
    assert reranker.first == 201 and result["selected"]["concept_id"] == 201
    assert result.get("method") != "auto_exact_name"
    assert "rerank" in [t["node"] for t in result["trace"]]


async def test_an_exact_name_match_needs_no_translation():
    class OriginalNameMatches(EnglishNameOnly):
        def match_name(self, names, vocabularies=None):
            from app.services.lexical import TextMatch
            from tests.workflows.helpers import CONCEPTS

            return TextMatch(concepts=[CONCEPTS[201]], via="name")

    translator = FakeTranslator({})
    ctx = make_context(RecordingReranker(), translator=translator, lexical=OriginalNameMatches())
    result = await run_pipeline(_state("type 2 diabetes mellitus"), ctx, "automap")
    assert result["method"] == "auto_exact_name" and translator.calls == []


class LanguageTranslator(FakeTranslator):
    async def translate(self, term, language=None):
        self.calls.append((term, language))
        return TranslationResult(self.mapping.get(term, term), "sl")


async def test_the_vocabulary_language_is_given_to_the_translator():
    translator = LanguageTranslator({"šećerna bolest tip 2": "type 2 diabetes mellitus"})
    ctx = make_context(RecordingReranker(), searcher=FakeSearcher(), translator=translator)
    state = _state("šećerna bolest tip 2")
    state["source_language"] = "hr"
    result = await run_pipeline(state, ctx, "automap")
    assert translator.calls == [("šećerna bolest tip 2", "hr")]
    # The trace keeps what the model detected next to the vocabulary's setting
    assert result["meta"]["source_language"] == "sl"
    assert result["meta"]["vocabulary_language"] == "hr"
    step = next(t for t in result["trace"] if t["node"] == "translate")
    assert "(sl, vocabulary: hr)" in str(step)


async def test_an_english_vocabulary_is_not_translated():
    translator = LanguageTranslator({})
    ctx = make_context(RecordingReranker(), searcher=FakeSearcher(), translator=translator)
    state = _state("type 2 diabetes")
    state["source_language"] = "en"
    result = await run_pipeline(state, ctx, "automap")
    assert translator.calls == [] and result["translated_query"] is None


async def test_the_language_goes_into_the_prompt_and_the_cache_key():
    from app.core.redis import get_redis
    from app.llm import structured

    prompts = []

    class FakeCompletions:
        async def create(self, **kwargs):
            prompts.append(kwargs["messages"][1]["content"])
            return structured.TranslationResponse(english="headache", language="hr")

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    translator = structured.Translator("test/language-model", client=FakeClient())
    assert translator._key("glavobolja") != translator._key("glavobolja", language="hr")
    get_redis().delete(translator._key("glavobolja"), translator._key("glavobolja", language="hr"))
    await translator.translate("glavobolja", language="hr")
    await translator.translate("glavobolja")
    assert "Source language (ISO 639-1) of the vocabulary: hr" in prompts[0]
    assert "Source language" not in prompts[1]
