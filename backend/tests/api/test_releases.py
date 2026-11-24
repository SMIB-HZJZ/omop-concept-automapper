"""Mapping releases: publishing locks a vocabulary, the next release unlocks it, and the OMOP
SOURCE_TO_CONCEPT_MAP keeps changed and removed mappings with an expired validity."""

import csv
import io

from sqlalchemy import text

from app.core.db import session_scope
from tests.conftest import import_source_csv, upload_vocabulary


def _state(client, headers, vid: int) -> dict:
    resp = client.get(f"/api/source-vocabularies/{vid}/releases", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sources(client, headers, vid: int) -> dict[str, int]:
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT source_value, source_id FROM source_concepts "
                "WHERE source_vocabulary_id = :v"
            ),
            {"v": vid},
        ).all()
    return dict(rows)


def _stcm(client, headers, vid: int, **params) -> list[dict]:
    resp = client.get(
        f"/api/source-vocabularies/{vid}/source_to_concept_map.csv", params=params, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return [
        (r["source_code"], int(r["target_concept_id"]), r["valid_start_date"],
         r["valid_end_date"], r["invalid_reason"])
        for r in csv.DictReader(io.StringIO(resp.text))
    ]  # fmt: skip


def test_release_locks_the_vocabulary_and_keeps_history(
    client, admin_headers, user_headers, loaded_vocabulary
):
    vid = 120
    import_source_csv(
        client,
        admin_headers,
        vid,
        content=b"source_value,source_concept_name,concept_id\nA,alpha,201\nB,beta,202\nC,gamma,\n",
    )
    ids = _sources(client, admin_headers, vid)
    state = _state(client, user_headers, vid)
    assert state["locked"] is False and state["releases"] == []
    assert state["draft"] == {"added": 2, "changed": 0, "removed": 0}

    url = f"/api/source-vocabularies/{vid}/releases"
    assert client.post(url, json={}, headers=user_headers).status_code == 403
    resp = client.post(url, json={"note": "  first   release "}, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    state = resp.json()
    assert state["locked"] is True and state["draft"] is None
    first = state["releases"][0]
    assert (first["number"], first["note"], first["mapped_sources"], first["added"]) == (
        1,
        "first release",
        2,
        2,
    )
    vocab = next(
        v
        for v in client.get("/api/source-vocabularies", headers=user_headers).json()
        if v["id"] == vid
    )
    assert vocab["locked"] is True and vocab["latest_release"] == 1

    # Locked: no mapping, unmapping, auto-map, import or second publish
    mapped = client.post(
        "/api/mappings", json={"source_id": ids["C"], "concept_ids": [201]}, headers=admin_headers
    )
    assert mapped.status_code == 409 and "locked" in mapped.json()["detail"]
    assert (
        client.post(
            "/api/mappings/unmap", json={"source_ids": [ids["A"]]}, headers=admin_headers
        ).status_code
        == 409
    )
    automap = client.post(
        f"/api/source-vocabularies/{vid}/automap",
        json={"domains": ["Condition"]},
        headers=admin_headers,
    )
    assert automap.status_code == 409
    more = client.post(
        f"/api/source-vocabularies/{vid}/concepts/import",
        files={"file": ("s.csv", b"source_value,source_concept_name\nD,delta\n", "text/csv")},
        headers=admin_headers,
    )
    assert more.status_code == 409
    assert client.post(url, json={}, headers=admin_headers).status_code == 409

    # The next release: remap A, remove B, map C
    assert client.post(f"{url}/next", headers=user_headers).status_code == 403
    assert client.post(f"{url}/next", headers=admin_headers).json()["locked"] is False
    assert client.post(f"{url}/next", headers=admin_headers).status_code == 409
    for source, concepts in (("A", [202]), ("C", [201])):
        resp = client.post(
            "/api/mappings",
            json={"source_id": ids[source], "concept_ids": concepts, "replace": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
    client.post("/api/mappings/unmap", json={"source_ids": [ids["B"]]}, headers=admin_headers)
    assert _state(client, admin_headers, vid)["draft"] == {"added": 1, "changed": 1, "removed": 1}
    # Before publishing, the latest release is still what the export gives by default
    assert {r[:2] for r in _stcm(client, admin_headers, vid)} == {("A", 201), ("B", 202)}

    assert client.post(url, json={}, headers=admin_headers).status_code == 201
    with session_scope() as session:  # release 1 was published earlier
        session.execute(
            text(
                "UPDATE mapping_release SET released_at = '2026-01-10' "
                "WHERE vocabulary_id = :v AND number = 1"
            ),
            {"v": vid},
        )
        day2 = session.execute(
            text(
                "SELECT to_char(released_at, 'YYYYMMDD') FROM mapping_release "
                "WHERE vocabulary_id = :v AND number = 2"
            ),
            {"v": vid},
        ).scalar_one()
        day_before = session.execute(
            text(
                "SELECT to_char(released_at - interval '1 day', 'YYYYMMDD') FROM mapping_release "
                "WHERE vocabulary_id = :v AND number = 2"
            ),
            {"v": vid},
        ).scalar_one()

    assert sorted(_stcm(client, admin_headers, vid)) == [
        ("A", 201, "20260110", day_before, "U"),  # remapped
        ("A", 202, day2, "20991231", ""),
        ("B", 202, "20260110", day_before, "D"),  # removed
        ("C", 201, day2, "20991231", ""),
    ]
    assert sorted(_stcm(client, admin_headers, vid, release="1")) == [
        ("A", 201, "20260110", "20991231", ""),
        ("B", 202, "20260110", "20991231", ""),
    ]
    assert (
        client.get(
            f"/api/source-vocabularies/{vid}/source_to_concept_map.csv",
            params={"release": "9"},
            headers=admin_headers,
        ).status_code
        == 404
    )
    history = _state(client, admin_headers, vid)["releases"]
    assert [(r["number"], r["added"], r["changed"], r["removed"]) for r in history] == [
        (2, 1, 1, 1),
        (1, 2, 0, 0),
    ]

    # Nothing changed: no empty release
    client.post(f"{url}/next", headers=admin_headers)
    nothing = client.post(url, json={}, headers=admin_headers)
    assert nothing.status_code == 409 and "Nothing changed" in nothing.json()["detail"]


def test_draft_export_previews_the_next_release(client, admin_headers, loaded_vocabulary):
    vid = 122
    import_source_csv(
        client,
        admin_headers,
        vid,
        content=b"source_value,source_concept_name,concept_id\nA,alpha,201\nB,beta,\n",
    )
    url = f"/api/source-vocabularies/{vid}/releases"
    client.post(url, json={}, headers=admin_headers)
    client.post(f"{url}/next", headers=admin_headers)
    ids = _sources(client, admin_headers, vid)
    client.post(
        "/api/mappings", json={"source_id": ids["B"], "concept_ids": [202]}, headers=admin_headers
    )
    draft = _stcm(client, admin_headers, vid, release="draft")
    assert {(r[0], r[1], r[3]) for r in draft} == {("A", 201, "20991231"), ("B", 202, "20991231")}
    assert {r[:2] for r in _stcm(client, admin_headers, vid)} == {("A", 201)}
    # A released (locked) vocabulary can still be deleted
    assert client.post(url, json={}, headers=admin_headers).status_code == 201
    assert (
        client.delete(f"/api/source-vocabularies/{vid}", headers=admin_headers).status_code == 200
    )


def test_vocabulary_update_opens_the_next_release(client, admin_headers, loaded_vocabulary):
    vid = 121
    import_source_csv(
        client,
        admin_headers,
        vid,
        content=b"source_value,source_concept_name,concept_id\nE11,dm2,501\nE12,dm2 b,201\n",
    )
    url = f"/api/source-vocabularies/{vid}/releases"
    assert client.post(url, json={}, headers=admin_headers).status_code == 201
    resp = upload_vocabulary(client, admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "SUCCESS", task
    assert task["result"]["released_vocabularies_reopened"] >= 1
    state = _state(client, admin_headers, vid)
    assert state["locked"] is False
    assert "OMOP vocabulary update" in state["draft_note"]
    # ICD10 501 was replaced by its Maps to target 201: a change for the next release
    assert state["draft"] == {"added": 0, "changed": 1, "removed": 0}
