from tests.conftest import import_source_csv


def _run(client, headers, vocabulary_id, **body):
    payload = {"domains": ["Drug", "Condition"], "threshold": 8, **body}
    resp = client.post(
        f"/api/source-vocabularies/{vocabulary_id}/automap", json=payload, headers=headers
    )
    assert resp.status_code == 202, resp.text
    return client.get(f"/api/tasks/{resp.json()['task_id']}", headers=headers).json()


def test_automap_validation(client, user_headers, embedded):
    import_source_csv(client, user_headers, 20)
    assert (
        client.post(
            "/api/source-vocabularies/20/automap", json={"domains": []}, headers=user_headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/source-vocabularies/20/automap",
            json={"domains": ["Drug"], "pipeline": "nope"},
            headers=user_headers,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/source-vocabularies/999/automap", json={"domains": ["Drug"]}, headers=user_headers
        ).status_code
        == 404
    )


def test_automap_review_mode_then_accept_and_reject(
    client, user_headers, embedded, fake_reranker, no_name_match
):
    import_source_csv(client, user_headers, 21)
    fake_reranker.overrides = {"headache": 5, "essential hypertension": None}
    task = _run(client, user_headers, 21, drug_specific=True)
    assert task["status"] == "SUCCESS", task
    result = task["result"]
    # 4 unmapped concepts (2 were pre-mapped): metformin, type 2 diabetes queued; headache below threshold
    assert result["processed"] == 4
    assert result["queued"] == 2 and result["mapped"] == 0
    assert result["outcomes"].get("no_match") == 1

    page = client.get("/api/source-vocabularies/21/suggestions", headers=user_headers).json()
    assert page["total"] == 2
    by_name = {s["source_concept_name"]: s for s in page["items"]}
    assert by_name["metformin 500 mg tableta"]["concept_id"] == 1
    assert by_name["metformin 500 mg tableta"]["method"] == "auto_drug"
    assert by_name["type 2 diabetes"]["concept_id"] == 201

    # Pending suggestions are shown on the unmapped list and skipped by a second run
    unmapped = client.get("/api/source-vocabularies/21/unmapped", headers=user_headers).json()
    assert any(i["suggestion_concept_id"] == 1 for i in unmapped["items"])
    again = _run(client, user_headers, 21, drug_specific=True)
    assert again["result"]["processed"] == 2

    accepted = client.post(
        "/api/suggestions/accept",
        json={"ids": [by_name["metformin 500 mg tableta"]["id"]]},
        headers=user_headers,
    )
    assert accepted.json()["count"] == 1
    rejected = client.post(
        "/api/suggestions/reject",
        json={"vocabulary_id": 21, "min_confidence": 1},
        headers=user_headers,
    )
    assert rejected.json()["count"] == 1

    stats = client.get("/api/source-vocabularies/21/stats", headers=user_headers).json()
    assert stats["mapped"] == 3 and stats["pending_suggestions"] == 0
    audit = {
        a["mapping_method"]: a
        for a in client.get("/api/source-vocabularies/21/audit/stats", headers=user_headers).json()
    }
    assert audit["auto_drug"]["mapping_count"] == 1 and audit["auto_drug"]["avg_confidence"] == 9
    history = client.get(
        "/api/source-vocabularies/21/suggestions",
        params={"status": "accepted"},
        headers=user_headers,
    ).json()
    assert history["items"][0]["decided_by"] == "mapper"


def test_automap_commit_mode(client, user_headers, embedded, fake_reranker, no_name_match):
    import_source_csv(client, user_headers, 22)
    task = _run(client, user_headers, 22, mode="commit", threshold=8)
    assert task["result"]["mapped"] == 4
    stats = client.get("/api/source-vocabularies/22/stats", headers=user_headers).json()
    assert stats["unmapped"] == 0
    recent = client.get("/api/source-vocabularies/22/audit/recent", headers=user_headers).json()
    assert {r["mapping_method"] for r in recent} == {"auto_standard"}
    assert all(r["pipeline"] == "automap" for r in recent)


def test_ai_suggest_single_concept(client, user_headers, embedded, fake_reranker, no_name_match):
    import_source_csv(client, user_headers, 23)
    item = client.get("/api/source-vocabularies/23/unmapped", headers=user_headers).json()["items"][
        0
    ]
    resp = client.post(
        f"/api/source-concepts/{item['source_id']}/suggest",
        json={"domains": ["Drug"], "drug_specific": True},
        headers=user_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "accepted"
    assert body["selected"]["concept_id"] == 1 and body["confidence"] == 9
    assert [t["node"] for t in body["trace"]] == [
        "extract_atc7",
        "match_previous",
        "match_code",
        "match_name",
        "retrieve",
        "rerank",
        "gate",
    ]
    assert "A10BA02" in body["trace"][0]["summary"]
    # Nothing is persisted
    assert (
        client.get("/api/source-vocabularies/23/stats", headers=user_headers).json()[
            "pending_suggestions"
        ]
        == 0
    )


def test_workflow_catalog(client, user_headers):
    catalog = client.get("/api/workflows", headers=user_headers).json()
    names = [p["name"] for p in catalog["pipelines"]]
    assert names[0] == "automap" and "automap_refine" in names
    assert {"retrieve", "llm_rerank", "confidence_gate", "agentic_refine"} <= {
        n["name"] for n in catalog["nodes"]
    }
