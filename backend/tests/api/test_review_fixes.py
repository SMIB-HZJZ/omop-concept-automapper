"""Regressions found in the review of 2.1: a publish racing a mapping, a stale confirmation of the
same concepts, match-type-only changes, and source codes listed twice in one vocabulary."""

import csv
import io
import threading
import time

from sqlalchemy import text

from app.core.db import session_scope
from app.services import mapping, releases
from tests.conftest import import_source_csv


def _ids(vid: int) -> dict[str, int]:
    with session_scope() as s:
        return dict(
            s.execute(
                text(
                    "SELECT source_value, source_id FROM source_concepts "
                    "WHERE source_vocabulary_id = :v ORDER BY source_id"
                ),
                {"v": vid},
            ).all()
        )


def test_publish_waits_for_a_mapping_in_progress(client, admin_headers, loaded_vocabulary):
    vid = 150
    import_source_csv(
        client,
        admin_headers,
        vid,
        b"source_value,source_concept_name,concept_id\nA,alpha,201\nB,beta,\n",
    )
    ids = _ids(vid)
    published: dict = {}
    with session_scope() as mapper:
        # A mapping is mid-transaction while an admin publishes
        mapping.map_concepts(mapper, ids["B"], [202], user_id=None, commit=False)

        def publish():
            with session_scope() as publisher:
                published["number"] = releases.publish(publisher, vid, None, None)

        worker = threading.Thread(target=publish)
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive(), "publish should wait for the mapping in progress"
        mapper.commit()
        worker.join(timeout=10)
    assert published == {"number": 1}
    with session_scope() as s:
        released = s.execute(
            text(
                "SELECT COUNT(*) FROM mapping_release_row r JOIN mapping_release m "
                "ON m.id = r.release_id WHERE m.vocabulary_id = :v"
            ),
            {"v": vid},
        ).scalar_one()
    assert released == 2  # the release contains the mapping that was in progress


def test_stale_confirmation_of_the_same_concepts(
    client, admin_headers, user_headers, loaded_vocabulary
):
    vid = 151
    import_source_csv(client, admin_headers, vid, b"source_value,source_concept_name\nA,dm2\n")
    term = _ids(vid)["A"]
    body = {"source_id": term, "concept_ids": [201], "match_type": "narrower"}
    assert client.post("/api/mappings", json=body, headers=user_headers).status_code == 200
    # Same concepts and match type from someone else: kept as it is, with its author
    assert client.post("/api/mappings", json=body, headers=admin_headers).status_code == 200
    row = client.get(f"/api/source-vocabularies/{vid}/mapped", headers=user_headers).json()
    assert (row["items"][0]["mapped_by"], row["items"][0]["match_type"]) == ("mapper", "narrower")
    # Same concepts with another match type: asked like any other change
    other = client.post(
        "/api/mappings", json={**body, "match_type": "exact"}, headers=admin_headers
    )
    assert other.status_code == 409
    assert other.json()["detail"]["match_types"] == ["narrower"]
    # The author can still undo it
    undo = client.post("/api/mappings/unmap", json={"source_ids": [term]}, headers=user_headers)
    assert undo.status_code == 200


def test_match_type_change_can_be_released(client, admin_headers, loaded_vocabulary):
    vid = 152
    import_source_csv(client, admin_headers, vid, b"source_value,source_concept_name\nA,dm2\n")
    term = _ids(vid)["A"]
    body = {"source_id": term, "concept_ids": [201], "match_type": "broader"}
    client.post("/api/mappings", json=body, headers=admin_headers)
    url = f"/api/source-vocabularies/{vid}/releases"
    assert client.post(url, json={}, headers=admin_headers).status_code == 201
    client.post(f"{url}/next", headers=admin_headers)
    client.post("/api/mappings", json={**body, "match_type": "exact"}, headers=admin_headers)
    state = client.get(url, headers=admin_headers).json()
    assert state["draft"] == {"added": 0, "changed": 1, "removed": 0}
    assert client.post(url, json={}, headers=admin_headers).status_code == 201


def _stcm(client, headers, vid: int) -> list[tuple]:
    resp = client.get(f"/api/source-vocabularies/{vid}/source_to_concept_map.csv", headers=headers)
    return sorted(
        (r["source_code"], int(r["target_concept_id"]), r["valid_end_date"], r["invalid_reason"])
        for r in csv.DictReader(io.StringIO(resp.text))
    )


def test_source_to_concept_map_history_is_per_source_code(client, admin_headers, loaded_vocabulary):
    """A code listed twice maps to the union of its rows' concepts; one-to-many stays valid."""
    vid = 153
    import_source_csv(
        client,
        admin_headers,
        vid,
        b"source_value,source_concept_name,concept_id\n"
        b"X,first,201\nX,second,201\nY,combined,201;202\n",
    )
    first_x = next(iter(_ids(vid).values()))
    url = f"/api/source-vocabularies/{vid}/releases"
    assert client.post(url, json={}, headers=admin_headers).status_code == 201
    assert _stcm(client, admin_headers, vid) == [
        ("X", 201, "20991231", ""),
        ("Y", 201, "20991231", ""),
        ("Y", 202, "20991231", ""),
    ]
    client.post(f"{url}/next", headers=admin_headers)
    client.post(
        "/api/mappings",
        json={"source_id": first_x, "concept_ids": [202], "replace": True},
        headers=admin_headers,
    )
    assert client.post(url, json={}, headers=admin_headers).status_code == 201
    # X still has 201 (through its second row) and gained 202: no contradiction, nothing ended
    assert _stcm(client, admin_headers, vid) == [
        ("X", 201, "20991231", ""),
        ("X", 202, "20991231", ""),
        ("Y", 201, "20991231", ""),
        ("Y", 202, "20991231", ""),
    ]


def test_mapping_during_vocabulary_update_waits_instead_of_deadlocking(
    client, admin_headers, loaded_vocabulary
):
    """The OMOP vocabulary update reopens a locked vocabulary (updating its row) and then changes
    its mappings; a mapping write on that vocabulary takes the vocabulary lock first, so it waits
    and then goes through, instead of a deadlock."""
    vid = 154
    import_source_csv(
        client, admin_headers, vid, b"source_value,source_concept_name,concept_id\nA,alpha,201\n"
    )
    term = _ids(vid)["A"]
    with session_scope() as s:
        releases.publish(s, vid, None, None)
    outcome: dict = {}

    def user():
        try:
            with session_scope() as s:
                mapping.map_concepts(s, term, [202], user_id=None)
            outcome["user"] = "ok"
        except Exception as exc:  # reported below
            outcome["user"] = type(exc).__name__

    with session_scope() as update:
        update.execute(
            text("UPDATE source_vocabulary SET locked = FALSE WHERE id = :v"), {"v": vid}
        )
        worker = threading.Thread(target=user)
        worker.start()
        time.sleep(0.5)
        update.execute(text("DELETE FROM source_standard_map WHERE source_id = :s"), {"s": term})
        update.commit()
    worker.join(timeout=10)
    assert outcome == {"user": "ok"}
