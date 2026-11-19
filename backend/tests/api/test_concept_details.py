"""Concept details (hierarchy, relationships, synonyms) and the AI explanation."""

from app.llm import explain as explain_module


def test_details_show_parents_children_synonyms_and_relationships(
    client, admin_headers, loaded_vocabulary
):
    # ibuprofen (9): parent ATC "ibuprofen; systemic" (103), child ibuprofen 400 MG tablet (4)
    d = client.get("/api/concepts/9/details", headers=admin_headers).json()
    assert d["concept"]["concept_name"] == "ibuprofen"
    assert [p["concept_id"] for p in d["parents"]] == [103]
    assert [c["concept_id"] for c in d["children"]] == [4]
    assert (d["parent_count"], d["child_count"]) == (1, 1)
    assert [(r["relationship_id"], r["concept_id"]) for r in d["related"]] == [("RxNorm ing of", 4)]

    # Only direct parents: 105 (Biguanides) is two levels above metformin 500 mg (1)
    d = client.get("/api/concepts/1/details", headers=admin_headers).json()
    assert [p["concept_id"] for p in d["parents"]] == [101]

    d = client.get("/api/concepts/201/details", headers=admin_headers).json()
    assert "Diabetes mellitus type 2" in d["synonyms"]
    assert [(r["relationship_id"], r["concept_id"]) for r in d["related"]] == [("Mapped from", 501)]


def test_details_of_an_unknown_concept_is_404(client, admin_headers, loaded_vocabulary):
    assert client.get("/api/concepts/999999/details", headers=admin_headers).status_code == 404


def test_explain_sends_the_vocabulary_data_and_caches(
    client, admin_headers, loaded_vocabulary, monkeypatch
):
    calls = []

    class Message:
        content = "What it is: a test explanation."

    class Response:
        choices = [type("Choice", (), {"message": Message()})()]

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return Response()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(explain_module, "get_async_client", lambda: Client())
    monkeypatch.setattr(explain_module, "PROMPT_VERSION", 9999)  # a fresh cache key
    first = client.post("/api/concepts/9/explain", headers=admin_headers).json()
    assert first["explanation"].startswith("What it is")
    assert first["cached"] is False
    user = calls[0]["messages"][1]["content"]
    assert "Concept: ibuprofen" in user
    assert "ibuprofen; systemic" in user  # parent
    assert "RxNorm ing of: ibuprofen 400 MG Oral Tablet" in user
    second = client.post("/api/concepts/9/explain", headers=admin_headers).json()
    assert second["cached"] is True and len(calls) == 1


def test_explain_failure_is_a_502(client, admin_headers, loaded_vocabulary, monkeypatch):
    class Completions:
        async def create(self, **kwargs):
            raise RuntimeError("provider down")

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(explain_module, "get_async_client", lambda: Client())
    monkeypatch.setattr(explain_module, "PROMPT_VERSION", 9998)
    resp = client.post("/api/concepts/205/explain", headers=admin_headers)
    assert resp.status_code == 502
