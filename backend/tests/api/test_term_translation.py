"""The mapping panel's English rendering of a term: same translator call (and cache key) as
auto-mapping's translate step, nothing for English vocabularies or unchanged terms."""

import pytest

from app.llm.structured import TranslationResult
from tests.conftest import import_source_csv

CSV = b"source_value,source_concept_name\nG1,glukoza u krvi\nG2,Headache\n"


class RecordingTranslator:
    def __init__(self, fail: bool = False):
        self.calls = []
        self.fail = fail

    async def translate(self, term, context=None, language=None):
        self.calls.append((term, context, language))
        if self.fail:
            raise RuntimeError("LLM down")
        english = {"glukoza u krvi": "blood glucose"}.get(term, term)
        return TranslationResult(english, language or "hr", cached=term == "Headache")


@pytest.fixture
def translator(monkeypatch):
    import app.workflows.service as service

    recording = RecordingTranslator()
    monkeypatch.setattr(service, "Translator", lambda model, effort=None: recording)
    return recording


def _ids(client, headers, vocabulary_id):
    items = client.get(
        f"/api/source-vocabularies/{vocabulary_id}/unmapped",
        params={"view": "all"},
        headers=headers,
    ).json()["items"]
    return {i["source_value"]: i["source_id"] for i in items}


def test_translation_uses_vocabulary_context_and_language(
    client, admin_headers, user_headers, translator
):
    import_source_csv(
        client,
        admin_headers,
        171,
        CSV,
        name="Lab tests",
        description="Hospital lab",
        source_language="hr",
    )
    ids = _ids(client, user_headers, 171)
    body = client.get(f"/api/source-concepts/{ids['G1']}/translation", headers=user_headers).json()
    assert body["english"] == "blood glucose" and body["language"] == "hr"
    term, context, language = translator.calls[-1]
    assert term == "glukoza u krvi" and "Lab tests" in context and language == "hr"
    # Already English: nothing to add
    body = client.get(f"/api/source-concepts/{ids['G2']}/translation", headers=user_headers).json()
    assert body["english"] is None and body["cached"] is True


def test_english_vocabulary_is_not_translated(client, admin_headers, user_headers, translator):
    import_source_csv(client, admin_headers, 172, CSV, source_language="en")
    ids = _ids(client, user_headers, 172)
    body = client.get(f"/api/source-concepts/{ids['G1']}/translation", headers=user_headers).json()
    assert body == {
        "source_id": ids["G1"],
        "english": None,
        "language": "en",
        "cached": False,
        "failed": False,
    }
    assert translator.calls == []


def test_failed_translation_is_reported_not_raised(
    client, admin_headers, user_headers, monkeypatch
):
    import app.workflows.service as service

    monkeypatch.setattr(
        service, "Translator", lambda model, effort=None: RecordingTranslator(fail=True)
    )
    import_source_csv(client, admin_headers, 173, CSV)
    ids = _ids(client, user_headers, 173)
    resp = client.get(f"/api/source-concepts/{ids['G1']}/translation", headers=user_headers)
    assert resp.status_code == 200 and resp.json()["failed"] is True
    assert (
        client.get("/api/source-concepts/999999/translation", headers=user_headers).status_code
        == 404
    )


def test_panel_calls_the_translator_like_the_translate_step(
    client, admin_headers, user_headers, translator
):
    """Same arguments as auto-mapping's translate step for the same term, so the panel finds the
    translations auto-mapping cached (the cache key is built from them)."""
    import asyncio

    from app.core.db import session_scope
    from app.llm import prompts
    from app.models import SourceVocabulary
    from app.workflows.nodes.translate import TranslateParams, translate
    from app.workflows.service import initial_state
    from tests.workflows.helpers import make_context

    for vid, form in ((174, {"source_language": "hr"}), (175, {})):
        import_source_csv(
            client, admin_headers, vid, CSV, name=f"Lab {vid}", description="Hospital lab", **form
        )
        ids = _ids(client, user_headers, vid)
        client.get(f"/api/source-concepts/{ids['G1']}/translation", headers=user_headers)
        from_panel = translator.calls[-1]
        # The state as the auto-map task builds it (worker/tasks/automap.py)
        with session_scope() as session:
            vocabulary = session.get(SourceVocabulary, vid)
            context = prompts.vocabulary_context(vocabulary.name, vocabulary.description)
            language = vocabulary.source_language
        state = initial_state(
            source_id=ids["G1"],
            source_value="G1",
            source_name="glukoza u krvi",
            domains=[],
            drug_specific=False,
            threshold=8,
            vocabulary_context=context,
            source_language=language,
        )
        before = len(translator.calls)
        asyncio.run(translate(state, make_context(None, translator=translator), TranslateParams()))
        assert len(translator.calls) == before + 1  # the step itself called the translator
        assert translator.calls[-1] == from_panel


def test_free_text_translation(client, user_headers, translator):
    resp = client.post("/api/translate", json={"text": " glukoza u krvi "}, headers=user_headers)
    assert resp.json()["english"] == "blood glucose" and resp.json()["source_id"] is None
    assert translator.calls[-1] == ("glukoza u krvi", None, None)
    assert (
        client.post("/api/translate", json={"text": "  "}, headers=user_headers).json()["english"]
        is None
    )
    assert (
        client.post("/api/translate", json={"text": "x" * 501}, headers=user_headers).status_code
        == 422
    )
