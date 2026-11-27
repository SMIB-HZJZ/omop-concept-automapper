"""Who may do what, presence on terms, team activity and the SOURCE_TO_CONCEPT_MAP export."""

import csv
import io

from tests.conftest import import_source_csv


def _vocab(client, admin_headers, vid: int) -> list[dict]:
    client.post(
        "/api/source-vocabularies",
        json={"id": vid, "name": f"Collab vocabulary {vid}"},
        headers=admin_headers,
    )
    import_source_csv(client, admin_headers, vid)
    return client.get(f"/api/source-vocabularies/{vid}/unmapped", headers=admin_headers).json()[
        "items"
    ]


def test_admin_only_endpoints(client, user_headers, admin_headers, loaded_vocabulary):
    _vocab(client, admin_headers, 93)
    for method, url, kwargs in (
        ("get", "/api/config", {}),
        ("get", "/api/config/models", {}),
        ("post", "/api/source-vocabularies", {"json": {"id": 94, "name": "x"}}),
        ("patch", "/api/source-vocabularies/93", {"json": {"is_drug": True}}),
        ("post", "/api/source-vocabularies/93/automap", {"json": {"domains": ["Condition"]}}),
        ("post", "/api/source-vocabularies/93/releases", {"json": {}}),
        ("post", "/api/source-vocabularies/93/releases/next", {}),
        ("delete", "/api/source-vocabularies/93", {}),
    ):
        resp = getattr(client, method)(url, headers=user_headers, **kwargs)
        assert resp.status_code == 403, (method, url, resp.status_code)
    # Verify & publish is open to users for review: the mapped set and its exports
    for url in (
        "/api/source-vocabularies/93/mapped",
        "/api/source-vocabularies/93/mapped.csv",
        "/api/source-vocabularies/93/source_to_concept_map.csv",
        "/api/source-vocabularies/93/releases",
    ):
        assert client.get(url, headers=user_headers).status_code == 200, url


def test_users_undo_only_their_own_mappings(client, user_headers, admin_headers, loaded_vocabulary):
    items = _vocab(client, admin_headers, 95)
    mine, theirs = items[0]["source_id"], items[1]["source_id"]
    assert (
        client.post(
            "/api/mappings", json={"source_id": mine, "concept_ids": [201]}, headers=user_headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/mappings", json={"source_id": theirs, "concept_ids": [202]}, headers=admin_headers
        ).status_code
        == 200
    )
    denied = client.post("/api/mappings/unmap", json={"source_ids": [theirs]}, headers=user_headers)
    assert denied.status_code == 403
    assert (
        client.post(
            "/api/mappings/unmap", json={"source_ids": [mine]}, headers=user_headers
        ).json()["count"]
        == 1
    )
    assert (
        client.post(
            "/api/mappings/unmap", json={"source_ids": [theirs]}, headers=admin_headers
        ).json()["count"]
        == 1
    )


def test_presence_and_activity(client, user_headers, admin_headers, loaded_vocabulary):
    items = _vocab(client, admin_headers, 96)
    a, b = items[0]["source_id"], items[1]["source_id"]
    client.put(f"/api/source-concepts/{a}/presence", headers=user_headers)
    seen = client.put(f"/api/source-concepts/{b}/presence", headers=admin_headers).json()
    assert {(p["username"], p["source_id"]) for p in seen} == {("mapper", a), ("admin", b)}
    # Opening another term moves the user's presence
    client.put(f"/api/source-concepts/{b}/presence", headers=user_headers)
    now = client.get("/api/source-vocabularies/96/presence", headers=user_headers).json()
    assert {(p["username"], p["source_id"]) for p in now} == {("mapper", b), ("admin", b)}
    client.delete("/api/source-vocabularies/96/presence", headers=user_headers)
    assert [
        p["username"]
        for p in client.get("/api/source-vocabularies/96/presence", headers=user_headers).json()
    ] == ["admin"]

    client.post("/api/mappings", json={"source_id": a, "concept_ids": [201]}, headers=user_headers)
    activity = {
        r["username"]: r
        for r in client.get("/api/source-vocabularies/96/activity", headers=user_headers).json()
    }
    assert activity["mapper"]["mapped_today"] == 1 and activity["mapper"]["mapped_total"] == 1


def test_source_to_concept_map_export(client, admin_headers, loaded_vocabulary):
    items = _vocab(client, admin_headers, 97)
    client.post(
        "/api/mappings",
        json={"source_id": items[0]["source_id"], "concept_ids": [201]},
        headers=admin_headers,
    )
    resp = client.get(
        "/api/source-vocabularies/97/source_to_concept_map.csv",
        params={"source_vocabulary_id": "HZJZ_TEST"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert list(rows[0]) == [
        "source_code",
        "source_concept_id",
        "source_vocabulary_id",
        "source_code_description",
        "target_concept_id",
        "target_vocabulary_id",
        "valid_start_date",
        "valid_end_date",
        "invalid_reason",
    ]
    row = rows[0]
    assert (row["source_code"], row["source_vocabulary_id"], row["target_concept_id"]) == (
        items[0]["source_value"],
        "HZJZ_TEST",
        "201",
    )
    assert row["source_concept_id"] == "0" and row["valid_end_date"] == "20991231"
    assert len(row["valid_start_date"]) == 8
    # Without a label the vocabulary name is used, cut to OMOP's 20 characters
    default = client.get(
        "/api/source-vocabularies/97/source_to_concept_map.csv", headers=admin_headers
    ).text
    assert "Collab_vocabulary_97" in default
