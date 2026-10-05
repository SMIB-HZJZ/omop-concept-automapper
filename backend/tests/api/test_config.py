def test_effective_config_defaults_to_env(client, user_headers):
    cfg = client.get("/api/config", headers=user_headers).json()
    assert cfg["llm_model"] == "test/chat"
    assert cfg["embedding_dims"] == 64
    assert cfg["collection_name"] == "test_embedder_64"
    assert all(v["source"] == "env" for v in cfg["values"])


def test_override_reset_and_stale_override(client, admin_headers, monkeypatch):
    resp = client.put("/api/config", json={"llm_model": "anthropic/claude"}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["llm_model"] == "anthropic/claude"
    source = {v["key"]: v["source"] for v in resp.json()["values"]}
    assert source["llm.model"] == "override"

    # Changing the env default (a provider switch) makes the old override stale and ignored
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_chat_model", "openai/other")
    cfg = client.get("/api/config", headers=admin_headers).json()
    assert cfg["llm_model"] == "openai/other"
    value = next(v for v in cfg["values"] if v["key"] == "llm.model")
    assert value["source"] == "env" and value["stale_override"] == "anthropic/claude"

    reset = client.post("/api/config/reset", headers=admin_headers).json()
    assert all(v["stale_override"] is None for v in reset["values"])


def test_setting_env_value_removes_override(client, admin_headers):
    client.put("/api/config", json={"embedding_dims": 512}, headers=admin_headers)
    cfg = client.put("/api/config", json={"embedding_dims": 64}, headers=admin_headers).json()
    assert next(v for v in cfg["values"] if v["key"] == "embedding.dims")["source"] == "env"


def test_collection_preview_and_models(client, user_headers):
    preview = client.get(
        "/api/config/collection-preview", params={"model": "openai/text-embedding-3-small", "dims": 512}, headers=user_headers
    ).json()
    assert preview == {"name": "openai_text_embedding_3_small_512", "exists": False, "embedded_count": None}
    models = client.get("/api/config/models", headers=user_headers).json()
    assert "test/chat" in models["llm_models"]
    assert models["gateway_models_available"] is False


def test_meta(client, user_headers):
    meta = client.get("/api/meta", headers=user_headers).json()
    assert {"Condition", "Drug", "Procedure"} <= set(meta["domains"])
    assert meta["dimension_options"] == [512, 1024, 1536, 2048, 3072]
