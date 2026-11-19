"""Deterministic text steps: source codes, exact names/synonyms, earlier mappings, keyword search."""

from app.services import lexical
from app.vector.store import SearchFilters
from tests.conftest import import_source_csv


def _automap(client, headers, vocabulary_id, **body):
    payload = {"domains": ["Condition"], "threshold": 8, "pipeline": "automap", **body}
    resp = client.post(
        f"/api/source-vocabularies/{vocabulary_id}/automap", json=payload, headers=headers
    )
    assert resp.status_code == 202, resp.text
    return client.get(f"/api/tasks/{resp.json()['task_id']}", headers=headers).json()


def _suggestions(client, headers, vocabulary_id):
    page = client.get(f"/api/source-vocabularies/{vocabulary_id}/suggestions", headers=headers)
    return {s["source_concept_name"]: s for s in page.json()["items"]}


def test_code_candidates():
    assert lexical.code_candidates(" e11.9 ") == ["E11.9", "E119"]
    assert lexical.code_candidates("E119-HR") == ["E119-HR", "E119", "E11.9"]
    assert lexical.code_candidates("") == []


def test_match_code_follows_maps_to(loaded_vocabulary):
    match = lexical.match_code("E11", ["ICD10"])
    assert [c["concept_id"] for c in match.concepts] == [201] and not match.ambiguous
    assert lexical.match_code("E11", ["ICD10CM"]).concepts == []


def test_match_name_uses_synonyms_case_and_accents(loaded_vocabulary):
    # Croatian synonym from CONCEPT_SYNONYM; accents and case are ignored
    assert [c["concept_id"] for c in lexical.match_name(["SECERNA BOLEST TIP 2"]).concepts] == [201]
    assert [c["concept_id"] for c in lexical.match_name(["headache!"]).concepts] == [203]
    # The ICD10 concept of the same name maps to the same standard concept: still one target
    assert [c["concept_id"] for c in lexical.match_name(["type 2 diabetes mellitus"]).concepts] == [
        201
    ]
    assert lexical.match_name(["no such thing"]).concepts == []


def test_keyword_search_finds_partial_words_and_synonyms(loaded_vocabulary):
    hits = lexical.keyword_search("hypertensio", 5, SearchFilters(domains=["Condition"]))
    assert hits[0]["concept_id"] == 202
    assert lexical.keyword_search("pyrexia", 5, SearchFilters())[0]["concept_id"] == 205
    assert lexical.keyword_search("hypertensio", 5, SearchFilters(domains=["Drug"])) == []


def test_pipeline_uses_code_then_name(client, admin_headers, user_headers, embedded, fake_reranker):
    csv = (
        "source_value,source_concept_name\n"
        "E11,nešto posve drugo\n"  # code match: ICD10 E11 -> Type 2 diabetes mellitus
        "X1,Cefalalgija\n"  # neither code nor name: search + LLM
        "X2,cephalalgia\n"  # synonym of Headache
    ).encode()
    import_source_csv(client, admin_headers, 60, csv)
    client.patch(
        "/api/source-vocabularies/60", json={"code_vocabularies": ["ICD10"]}, headers=admin_headers
    )
    task = _automap(client, admin_headers, 60)
    assert task["status"] == "SUCCESS", task
    found = _suggestions(client, user_headers, 60)
    assert found["nešto posve drugo"]["method"] == "auto_code"
    assert found["nešto posve drugo"]["concept_id"] == 201
    assert found["nešto posve drugo"]["confidence"] == 10
    assert found["cephalalgia"]["method"] == "auto_exact_name"
    assert found["cephalalgia"]["concept_id"] == 203


def test_restricted_domains_reject_text_matches_elsewhere(
    client, user_headers, embedded, fake_reranker, admin_headers
):
    import_source_csv(client, admin_headers, 61, b"source_value,source_concept_name\nX,Headache\n")
    _automap(client, admin_headers, 61, domains=["Drug"], domain_mode="restrict")
    assert (
        _suggestions(client, user_headers, 61).get("Headache", {}).get("method")
        != "auto_exact_name"
    )


def test_earlier_reviewed_mapping_is_reused(
    client, admin_headers, user_headers, embedded, fake_reranker
):
    # Vocabulary 62 maps "Glavobolja" by hand (pre-mapping in the CSV counts as reviewed)
    import_source_csv(
        client,
        admin_headers,
        62,
        b"source_value,source_concept_name,concept_id\nG1,Glavobolja,203\n",
    )
    import_source_csv(
        client, admin_headers, 63, b"source_value,source_concept_name\nG9,glavobolja\n"
    )
    _automap(client, admin_headers, 63)
    found = _suggestions(client, user_headers, 63)["glavobolja"]
    assert (found["method"], found["concept_id"], found["confidence"]) == ("auto_previous", 203, 10)


def test_similar_examples(client, user_headers, embedded, admin_headers):
    import_source_csv(
        client,
        admin_headers,
        64,
        b"source_value,source_concept_name,concept_id\nB1,akutni bronhitis,204\n",
    )
    examples = lexical.similar_examples("akutni bronhitis lijevo", None)
    assert examples and examples[0]["source_name"] == "akutni bronhitis"
    assert "Acute bronchitis" in examples[0]["mapped_to"]


def test_rank_fusion_prefers_concepts_found_by_both_searches():
    from app.workflows.nodes.retrieve import _fuse

    vector = [{"concept_id": i, "score": 1 - i / 10, "source": "vector"} for i in (1, 2, 3)]
    keyword = [{"concept_id": i, "score": 0.5, "source": "keyword"} for i in (3, 4)]
    fused = _fuse([vector, keyword], 3, 60)
    assert [r["concept_id"] for r in fused] == [3, 1, 2]
    assert fused[0]["source"] == "vector"  # the vector score is kept for display
