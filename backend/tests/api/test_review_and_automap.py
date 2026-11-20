from tests.conftest import import_source_csv


def _run(client, headers, vocabulary_id, **body):
    payload = {"domains": ["Drug", "Condition"], "threshold": 8, **body}
    resp = client.post(
        f"/api/source-vocabularies/{vocabulary_id}/automap", json=payload, headers=headers
    )
    assert resp.status_code == 202, resp.text
    return client.get(f"/api/tasks/{resp.json()['task_id']}", headers=headers).json()


def test_automap_validation(client, admin_headers, user_headers, embedded):
    import_source_csv(client, admin_headers, 20)
    assert (
        client.post(
            "/api/source-vocabularies/20/automap", json={"domains": []}, headers=admin_headers
        ).status_code
        == 422
    )
    # "All domains" needs no domain list
    all_domains = client.post(
        "/api/source-vocabularies/20/automap",
        json={"domains": [], "domain_mode": "all", "pipeline": "nope"},
        headers=admin_headers,
    )
    assert all_domains.status_code == 400, all_domains.text  # past validation, stopped at pipeline
    assert (
        client.post(
            "/api/source-vocabularies/20/automap",
            json={"domains": ["Drug"], "pipeline": "nope"},
            headers=admin_headers,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/source-vocabularies/999/automap",
            json={"domains": ["Drug"]},
            headers=admin_headers,
        ).status_code
        == 404
    )


def test_automap_review_mode_then_accept_and_reject(
    client, user_headers, embedded, fake_reranker, no_name_match, admin_headers
):
    import_source_csv(client, admin_headers, 21)
    fake_reranker.overrides = {"headache": 5, "essential hypertension": None}
    task = _run(client, admin_headers, 21, drug_specific=True)
    assert task["status"] == "SUCCESS", task
    result = task["result"]
    # 4 unmapped concepts (2 were pre-mapped): metformin, type 2 diabetes queued; headache below threshold
    assert result["processed"] == 4
    assert result["queued"] == 2 and result["mapped"] == 0
    assert result["outcomes"].get("no_match") == 1
    assert result["below_threshold"] == 1  # headache: kept for the term list, not queued

    page = client.get("/api/source-vocabularies/21/suggestions", headers=user_headers).json()
    assert page["total"] == 2
    by_name = {s["source_concept_name"]: s for s in page["items"]}
    assert by_name["metformin 500 mg tableta"]["concept_id"] == 1
    assert by_name["metformin 500 mg tableta"]["method"] == "auto_drug"
    assert by_name["type 2 diabetes"]["concept_id"] == 201

    # Terms waiting in Review are under "AI suggested", not "To do", and a second run skips them
    url = "/api/source-vocabularies/21/unmapped"
    suggested = client.get(url, params={"view": "suggested"}, headers=user_headers).json()
    assert any(i["suggestion_concept_id"] == 1 for i in suggested["items"])
    todo = client.get(url, headers=user_headers).json()
    assert all(i["suggestion_id"] is None for i in todo["items"]) and todo["items"]
    assert todo["todo"] == todo["total"] == suggested["todo"]
    again = _run(client, admin_headers, 21, drug_specific=True)
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


def test_automap_keeps_the_latest_pick_below_the_threshold(
    client, admin_headers, user_headers, embedded, fake_reranker, no_name_match
):
    import_source_csv(client, admin_headers, 24)
    fake_reranker.overrides = {"headache": 5}
    task = _run(client, admin_headers, 24)
    assert task["result"]["below_threshold"] == 1, task["result"]

    # Not in the review queue, but on the term list with its pick and confidence
    review = client.get("/api/source-vocabularies/24/suggestions", headers=user_headers).json()
    assert "headache" not in {s["source_concept_name"] for s in review["items"]}
    unmapped = client.get("/api/source-vocabularies/24/unmapped", headers=user_headers).json()
    item = next(i for i in unmapped["items"] if i["source_concept_name"] == "headache")
    assert item["low_confidence"] == 5 and item["low_concept_name"]
    assert item["suggestion_id"] is None
    low = client.get(
        "/api/source-vocabularies/24/suggestions",
        params={"status": "below_threshold", "source_id": item["source_id"]},
        headers=user_headers,
    ).json()
    assert low["total"] == 1 and low["items"][0]["confidence"] == 5

    # The next run tries the term again; the latest result replaces the old one
    fake_reranker.overrides = {"headache": 6}
    again = _run(client, admin_headers, 24)
    assert again["result"]["processed"] == 1  # only headache is left (the rest is queued)
    low = client.get(
        "/api/source-vocabularies/24/suggestions",
        params={"status": "below_threshold"},
        headers=user_headers,
    ).json()
    assert [s["confidence"] for s in low["items"]] == [6]

    # A confident pick queues it for review and drops the hint
    fake_reranker.overrides = {}
    _run(client, admin_headers, 24)
    unmapped = client.get(
        "/api/source-vocabularies/24/unmapped", params={"view": "all"}, headers=user_headers
    ).json()
    item = next(i for i in unmapped["items"] if i["source_concept_name"] == "headache")
    assert item["low_concept_id"] is None and item["suggestion_id"] is not None


def test_automap_commit_mode(
    client, admin_headers, user_headers, embedded, fake_reranker, no_name_match
):
    import_source_csv(client, admin_headers, 22)
    task = _run(client, admin_headers, 22, mode="commit", threshold=8)
    assert task["result"]["mapped"] == 4
    stats = client.get("/api/source-vocabularies/22/stats", headers=user_headers).json()
    assert stats["unmapped"] == 0
    recent = client.get("/api/source-vocabularies/22/audit/recent", headers=user_headers).json()
    assert {r["mapping_method"] for r in recent} == {"auto_standard"}
    assert all(r["pipeline"] == "automap" for r in recent)


def test_ai_suggest_single_concept(
    client, admin_headers, user_headers, embedded, fake_reranker, no_name_match
):
    import_source_csv(client, admin_headers, 23)
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
        "translate",
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
