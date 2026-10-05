def test_search_requires_collection(client, user_headers, loaded_vocabulary):
    from app.core.db import session_scope
    from app.vector import store

    with session_scope() as session:
        if store.collection_exists(session, "test_embedder_64"):
            return  # another test already embedded
    resp = client.post("/api/search", json={"query": "metformin"}, headers=user_headers)
    assert resp.status_code == 409


def test_embed_status_and_index(client, admin_headers, embedded):
    assert embedded["result"]["embedded"] == 17  # 19 standard concepts minus box + marketed product
    status = client.get("/api/embeddings/status", headers=admin_headers).json()
    assert status["exists"] and status["embedded"] == status["total"] == 17
    assert status["percentage"] == 100.0 and status["dims"] == 64

    drug = client.get("/api/embeddings/status", params={"domain": "Drug"}, headers=admin_headers).json()
    assert drug["total"] == 7

    from app.core.db import session_scope
    from app.vector import store

    with session_scope() as session:
        col = store.get_collection(session, "test_embedder_64")
        assert store.has_index(session, col.table_name)

    # Nothing left to embed
    resp = client.post("/api/embeddings/embed", json={}, headers=admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["result"]["embedded"] == 0


def test_search_filters(client, user_headers, embedded):
    resp = client.post(
        "/api/search", json={"query": "metformin 500 mg oral tablet", "k": 5}, headers=user_headers
    ).json()
    assert resp["method"] == "Standard similarity"
    assert resp["results"][0]["concept_name"] == "metformin 500 MG Oral Tablet"
    assert [r["rank"] for r in resp["results"]] == [1, 2, 3, 4, 5]
    assert resp["results"][0]["atc7_codes"] == ["A10BA02"]

    cond = client.post(
        "/api/search", json={"query": "diabetes", "domains": ["Condition"], "k": 50}, headers=user_headers
    ).json()["results"]
    assert cond and all(r["domain_id"] == "Condition" for r in cond)
    assert all(r["standard_concept"] == "S" for r in cond)  # the ICD10 concept is never returned

    atc = client.post(
        "/api/search", json={"query": "tablet", "atc7_codes": ["M01AE01"], "k": 50}, headers=user_headers
    ).json()
    assert atc["method"] == "ATC7 filtered (M01AE01)"
    assert {r["concept_id"] for r in atc["results"]} == {4, 9}

    vocab = client.post(
        "/api/search", json={"query": "hemoglobin", "vocabulary_id": "LOINC"}, headers=user_headers
    ).json()["results"]
    assert {r["vocabulary_id"] for r in vocab} == {"LOINC"}

    bad = client.post("/api/search", json={"query": "x", "atc7_codes": ["A10"]}, headers=user_headers)
    assert bad.status_code == 422


def test_query_embeddings_are_cached(client, user_headers, embedded, fake_embedder):
    client.post("/api/search", json={"query": "unique cached query"}, headers=user_headers)
    client.post("/api/search", json={"query": "unique cached query"}, headers=user_headers)
    assert sum(1 for c in fake_embedder if c == ["unique cached query"]) == 1


def test_sync_tasks(client, admin_headers, embedded):
    resp = client.post("/api/embeddings/sync-metadata", headers=admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "SUCCESS" and task["result"]["removed"] == 0

    resp = client.post("/api/embeddings/sync-atc7", headers=admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["result"]["embedded_with_atc7"] == 7  # 9 drugs with ATC7 minus box + marketed


def test_clear_embeddings_in_other_collection(client, admin_headers, embedded):
    # Switch to another embedding space, embed one domain, clear it, switch back
    client.put("/api/config", json={"embedding_dims": 32}, headers=admin_headers)
    try:
        resp = client.post("/api/embeddings/embed", json={"domain": "Condition"}, headers=admin_headers)
        task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
        assert task["result"]["embedded"] == 5
        status = client.get("/api/embeddings/status", headers=admin_headers).json()
        assert status["collection"] == "test_embedder_32" and status["embedded"] == 5
        assert client.delete("/api/embeddings", headers=admin_headers).status_code == 200
        assert client.get("/api/embeddings/status", headers=admin_headers).json()["exists"] is False
    finally:
        client.post("/api/config/reset", headers=admin_headers)
    assert client.get("/api/embeddings/status", headers=admin_headers).json()["embedded"] == 17
