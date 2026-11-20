"""A new vocabulary needs a name; the description is optional and editable."""

from tests.conftest import FIXTURES


def _import(client, headers, vid, **form):
    return client.post(
        f"/api/source-vocabularies/{vid}/concepts/import",
        files={"file": ("s.csv", (FIXTURES / "source_concepts.csv").read_bytes(), "text/csv")},
        data=form,
        headers=headers,
    )


def _vocab(client, headers, vid):
    return next(
        v for v in client.get("/api/source-vocabularies", headers=headers).json() if v["id"] == vid
    )


def test_name_required_description_optional(client, admin_headers):
    assert _import(client, admin_headers, 161).status_code == 400
    assert _import(client, admin_headers, 161, name="   ").status_code == 400
    assert (
        client.post(
            "/api/source-vocabularies", json={"id": 162, "name": " "}, headers=admin_headers
        ).status_code
        == 422
    )
    resp = _import(
        client,
        admin_headers,
        161,
        name="Lab orders",
        description="  Laboratory tests ordered;\n the term names the test, not its result. ",
    )
    assert resp.status_code == 202, resp.text
    v = _vocab(client, admin_headers, 161)
    assert v["name"] == "Lab orders"
    assert v["description"] == "Laboratory tests ordered; the term names the test, not its result."

    # Appending to an existing vocabulary needs no name and keeps the description
    assert _import(client, admin_headers, 161).status_code == 202
    assert _vocab(client, admin_headers, 161)["description"].startswith("Laboratory")

    # Edited later; an empty description clears it
    resp = client.patch(
        "/api/source-vocabularies/161", json={"description": ""}, headers=admin_headers
    )
    assert resp.status_code == 200 and resp.json()["description"] is None
    too_long = client.patch(
        "/api/source-vocabularies/161", json={"description": "x" * 501}, headers=admin_headers
    )
    assert too_long.status_code == 422
    client.delete("/api/source-vocabularies/161", headers=admin_headers)


def test_users_cannot_edit_the_description(client, user_headers, admin_headers):
    client.post("/api/source-vocabularies", json={"id": 163, "name": "x"}, headers=admin_headers)
    resp = client.patch(
        "/api/source-vocabularies/163", json={"description": "y"}, headers=user_headers
    )
    assert resp.status_code == 403
    client.delete("/api/source-vocabularies/163", headers=admin_headers)


def test_new_vocabularies_get_the_next_id(client, admin_headers):
    first = client.post(
        "/api/source-vocabularies", json={"name": "Auto A"}, headers=admin_headers
    ).json()
    resp = client.post(
        "/api/source-vocabularies/import",
        files={"file": ("s.csv", (FIXTURES / "source_concepts.csv").read_bytes(), "text/csv")},
        data={"name": "Auto B", "description": "Discharge diagnoses", "is_drug": "false"},
        headers=admin_headers,
    )
    assert resp.status_code == 202, resp.text
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["result"]["vocabulary_id"] == first["id"] + 1
    second = _vocab(client, admin_headers, first["id"] + 1)
    assert (second["name"], second["description"]) == ("Auto B", "Discharge diagnoses")
    assert second["concept_count"] > 0

    # A name is required for a new vocabulary
    no_name = client.post(
        "/api/source-vocabularies/import",
        files={"file": ("s.csv", (FIXTURES / "source_concepts.csv").read_bytes(), "text/csv")},
        headers=admin_headers,
    )
    assert no_name.status_code == 422
    for vid in (first["id"], first["id"] + 1):
        client.delete(f"/api/source-vocabularies/{vid}", headers=admin_headers)
