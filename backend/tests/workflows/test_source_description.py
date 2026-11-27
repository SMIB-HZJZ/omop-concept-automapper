"""A source term's own description (CSV column source_description) reaches the LLM steps."""

import pytest

from app.llm import structured
from app.services.source_import import SourceImportError, parse_description
from app.workflows.service import initial_state, run_pipeline
from app.workflows.state import term_notes
from tests.workflows.helpers import FakeSearcher, make_context
from tests.workflows.test_translate import RecordingReranker
from tests.workflows.test_vocabulary_context import _Capture

LINE = "Description of this source term (from the source list): "


def _state(description):
    return initial_state(
        source_id=1,
        source_value="J45",
        source_name="type 2 diabetes",
        source_description=description,
        domains=["Condition"],
        drug_specific=False,
        threshold=8,
    )


def test_parse_description():
    assert parse_description("  Use for\n acute attacks ") == "Use for acute attacks"
    assert parse_description("") is None and parse_description(None) is None
    with pytest.raises(SourceImportError, match="at most"):
        parse_description("x" * 2001)


def test_term_notes_put_the_description_first():
    state = _state(" Use only\nfor confirmed asthma ")
    assert state["source_description"] == "Use only for confirmed asthma"
    state["notes"] = ["Parsed drug structure: ..."]
    assert term_notes(state) == [
        LINE + "Use only for confirmed asthma",
        "Parsed drug structure: ...",
    ]
    assert term_notes(_state(None)) == []


async def test_reranker_gets_the_description():
    reranker = RecordingReranker()
    await run_pipeline(
        _state("Use only for confirmed asthma"),
        make_context(reranker, searcher=FakeSearcher()),
        "automap",
    )
    assert reranker.kwargs[0]["notes"][0] == LINE + "Use only for confirmed asthma"

    plain = RecordingReranker()
    await run_pipeline(_state(None), make_context(plain, searcher=FakeSearcher()), "automap")
    assert "notes" not in plain.kwargs[0]


async def test_translator_and_rewriter_prompts(monkeypatch):
    translator = structured.Translator("test/desc-model")
    assert translator._key("Astma") != translator._key("Astma", description="Acute attacks")
    assert translator._key("Astma") == translator._key("Astma", description=None)

    monkeypatch.setattr(structured, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError()))
    client = _Capture(structured.QueryRewriteResponse(queries=["asthma attack"]))
    rewriter = structured.QueryRewriter("test/desc-model", client=client)
    await rewriter.rewrite("Astma", description="Acute attacks")
    assert LINE + "Acute attacks\n" in client.messages[0][1]["content"]

    client = _Capture(structured.TranslationResponse(english="asthma", language="hr"))
    translator = structured.Translator("test/desc-model", client=client)
    await translator.translate("Astma", description="Acute attacks")
    assert client.messages[0][1]["content"] == "Term: Astma\n" + LINE + "Acute attacks"
