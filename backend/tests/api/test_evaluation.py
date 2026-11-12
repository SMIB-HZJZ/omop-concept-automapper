"""`python -m app.cli evaluate`: graded against existing mappings, nothing written."""

import json

from app.services import evaluation
from tests.conftest import import_source_csv

CSV = (
    b"source_value,source_concept_name,freq,concept_id\n"
    b"X1,secerna bolest,1,201\n"  # Croatian-ish: only found after translation
    b"X2,type 2 diabetes,1,201\n"
    b"X3,headache,1,\n"  # unmapped: not part of the gold set
)


class OverlapReranker:
    """Picks the candidate sharing most words with the term (or its translation); none if no
    candidate shares a word, as a careful LLM would refuse an unrelated concept."""

    async def rerank(self, term, candidates, drug_specific, translation=None):
        import re

        from app.llm.structured import RerankResult

        words = set(re.findall(r"[a-z0-9]+", f"{term} {translation or ''}".lower()))
        overlaps = [
            len(words & set(re.findall(r"[a-z0-9]+", c["concept_name"].lower())))
            for c in candidates
        ]
        best = max(range(len(candidates)), key=overlaps.__getitem__)
        return RerankResult(best, 9) if overlaps[best] else RerankResult(None, None)


def test_evaluate_compares_translation(client, user_headers, embedded, monkeypatch, tmp_path):
    from app import cli
    from app.core.db import get_engine
    from app.llm import structured
    from tests.workflows.test_translate import FakeTranslator

    import_source_csv(client, user_headers, 40, CSV)
    translator = FakeTranslator({"secerna bolest": "type 2 diabetes mellitus"})
    monkeypatch.setattr(structured, "Reranker", lambda model: OverlapReranker())
    monkeypatch.setattr(structured, "Translator", lambda model: translator)

    from sqlmodel import Session

    with Session(get_engine()) as session:
        items = evaluation.load_gold(session, 40, sample=10)
    assert {i.source_name for i in items} == {"secerna bolest", "type 2 diabetes"}

    with Session(get_engine()) as session:
        before = session.exec(
            __import__("sqlalchemy").text("SELECT COUNT(*) FROM source_standard_map")
        ).one()[0]
    cli.main(["evaluate", "40", "--out", str(tmp_path), "--domains", "Condition"])
    with Session(get_engine()) as session:
        after = session.exec(
            __import__("sqlalchemy").text("SELECT COUNT(*) FROM source_standard_map")
        ).one()[0]
    assert before == after  # nothing persisted

    report = json.loads((tmp_path / "summary.json").read_text())
    by_pipeline = {v["pipeline"]: v for v in report["variants"]}
    assert by_pipeline["automap"]["top1_accuracy"] == 0.5
    assert by_pipeline["automap_translated"]["top1_accuracy"] == 1.0
    comparison = report["comparisons"][0]
    assert comparison["correct"]["only_b"] == 1 and comparison["correct"]["only_a"] == 0
    assert (tmp_path / "items.csv").read_text().count("\n") == 5  # header + 2 items x 2 variants


def test_unreviewed_auto_mappings_are_not_gold(client, user_headers, embedded, fake_reranker):
    from sqlmodel import Session

    from app.core.db import get_engine

    import_source_csv(client, user_headers, 41, b"source_value,source_concept_name\nX9,headache\n")
    resp = client.post(
        "/api/source-vocabularies/41/automap",
        json={"domains": ["Condition"], "mode": "commit"},
        headers=user_headers,
    )
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=user_headers).json()
    assert task["result"]["mapped"] == 1
    with Session(get_engine()) as session:
        assert evaluation.load_gold(session, 41, sample=10) == []
        assert len(evaluation.load_gold(session, 41, 10, include_unreviewed_auto=True)) == 1


def test_sign_test():
    a = [evaluation.ItemResult("a", i, correct=i < 2) for i in range(12)]
    b = [evaluation.ItemResult("b", i, correct=i < 10) for i in range(12)]
    result = evaluation.sign_test(a, b)
    assert (result["only_a"], result["only_b"]) == (0, 8)
    assert result["p_value"] == round(2 / 2**8, 4)
