"""Embedding several domains in one run, and status for several domains."""


def test_status_for_several_domains(client, admin_headers, embedded):
    one = client.get(
        "/api/embeddings/status", params={"domain": "Condition"}, headers=admin_headers
    ).json()
    two = client.get(
        "/api/embeddings/status",
        params=[("domain", "Condition"), ("domain", "Measurement")],
        headers=admin_headers,
    ).json()
    assert two["total"] > one["total"] > 0


def test_embed_several_domains_in_one_run(client, admin_headers, embedded):
    client.put("/api/config", json={"embedding_dims": 24}, headers=admin_headers)
    try:
        resp = client.post(
            "/api/embeddings/embed",
            json={"domains": ["Condition", "Measurement"], "batch_size": 3},
            headers=admin_headers,
        )
        assert resp.status_code == 422  # batch_size below the minimum
        resp = client.post(
            "/api/embeddings/embed",
            json={"domains": ["Condition", "Measurement"], "batch_size": 10},
            headers=admin_headers,
        )
        task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
        assert task["status"] == "SUCCESS", task
        status = client.get(
            "/api/embeddings/status",
            params=[("domain", "Condition"), ("domain", "Measurement")],
            headers=admin_headers,
        ).json()
        assert task["result"]["embedded"] == status["total"] == 7  # 5 conditions + 2 measurements
        assert status["embedded"] == 7
    finally:
        client.delete(
            "/api/embeddings", params={"confirm": "test_embedder_24"}, headers=admin_headers
        )
        client.post("/api/config/reset", headers=admin_headers)
