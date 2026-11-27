"""Threshold calibration from review decisions and the priority order of the review queue."""

from sqlalchemy import text

from app.core.db import get_engine
from tests.conftest import import_source_csv

CSV = (
    b"source_value,source_concept_name,freq\n"
    b"C1,cal one,10\nC2,cal two,1\nC3,cal three,500\nC4,cal four,5\n"
)


def _suggest(rows):
    with get_engine().begin() as conn:
        for name, confidence, status, method in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO mapping_suggestion (source_id, concept_id, confidence, method, status)
                    SELECT source_id, 203, :c, :m, :s FROM source_concepts
                    WHERE source_concept_name = :n AND source_vocabulary_id = 70
                    """
                ),
                {"n": name, "c": confidence, "s": status, "m": method},
            )


def test_calibration_recommends_a_threshold(client, admin_headers, user_headers, loaded_vocabulary):
    import_source_csv(client, admin_headers, 70, CSV)
    _suggest(
        [
            ("cal one", 10, "accepted", "auto_code"),
            ("cal two", 9, "accepted", "auto_standard"),
            ("cal three", 8, "rejected", "auto_standard"),
            ("cal four", 7, "rejected", "auto_standard"),
        ]
    )
    cal = client.get(
        "/api/suggestions/calibration", params={"vocabulary_id": 70}, headers=user_headers
    ).json()
    assert cal["decided"] == 4 and cal["recommended_threshold"] == 9
    assert [lv["precision_at_or_above"] for lv in cal["levels"]] == [1.0, 1.0, 2 / 3, 0.5]
    methods = {m["method"]: m["precision"] for m in cal["methods"]}
    assert methods == {"auto_code": 1.0, "auto_standard": 1 / 3}


def test_priority_sort_puts_frequent_uncertain_terms_first(
    client, admin_headers, user_headers, loaded_vocabulary
):
    import_source_csv(client, admin_headers, 71, CSV.replace(b"cal", b"pri"))
    with get_engine().begin() as conn:
        for name, confidence in (
            ("pri one", 9),
            ("pri two", 6),
            ("pri three", 10),
            ("pri four", 2),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO mapping_suggestion (source_id, concept_id, confidence, method)
                    SELECT source_id, 203, :c, 'auto_standard' FROM source_concepts
                    WHERE source_concept_name = :n AND source_vocabulary_id = 71
                    """
                ),
                {"n": name, "c": confidence},
            )
    page = client.get(
        "/api/source-vocabularies/71/suggestions", params={"sort": "priority"}, headers=user_headers
    ).json()
    # freq x (11 - confidence): three 500x1, four 5x9, one 10x2, two 1x5
    assert [s["source_concept_name"] for s in page["items"]] == [
        "pri three",
        "pri four",
        "pri one",
        "pri two",
    ]


def _status(name):
    with get_engine().connect() as conn:
        return conn.execute(
            text(
                """
                SELECT ms.status, ms.prior_status FROM mapping_suggestion ms
                JOIN source_concepts sc USING (source_id)
                WHERE sc.source_concept_name = :n AND sc.source_vocabulary_id = 72
                """
            ),
            {"n": name},
        ).all()


def test_manual_mapping_decides_ai_picks(client, admin_headers, user_headers, loaded_vocabulary):
    """Terms mapped by hand count in calibration, including picks under the threshold; Undo
    (unmap) puts the picks back."""
    import_source_csv(client, admin_headers, 72, CSV.replace(b"cal", b"man"))
    _suggest_in = [
        ("man one", 6, "below_threshold"),  # mapped to the AI's concept -> accepted
        ("man two", 5, "below_threshold"),  # mapped to another concept -> rejected
        ("man three", 9, "pending"),  # overridden in Map -> rejected
    ]
    with get_engine().begin() as conn:
        for name, confidence, status in _suggest_in:
            conn.execute(
                text(
                    """
                    INSERT INTO mapping_suggestion (source_id, concept_id, confidence, method, status)
                    SELECT source_id, 203, :c, 'auto_standard', :s FROM source_concepts
                    WHERE source_concept_name = :n AND source_vocabulary_id = 72
                    """
                ),
                {"n": name, "c": confidence, "s": status},
            )
        ids = dict(
            conn.execute(
                text(
                    "SELECT source_concept_name, source_id FROM source_concepts"
                    " WHERE source_vocabulary_id = 72"
                )
            ).all()
        )

    def map_to(name, concept_id, replace=False):
        resp = client.post(
            "/api/mappings",
            json={"source_id": ids[name], "concept_ids": [concept_id], "replace": replace},
            headers=user_headers,
        )
        assert resp.status_code == 200, resp.text

    def calibration():
        return client.get(
            "/api/suggestions/calibration", params={"vocabulary_id": 72}, headers=user_headers
        ).json()

    map_to("man one", 203)
    map_to("man two", 201)
    map_to("man three", 201)
    assert _status("man one") == [("accepted", "below_threshold")]
    assert _status("man two") == [("rejected", "below_threshold")]
    assert _status("man three") == [("rejected", "pending")]
    cal = calibration()
    assert cal["decided"] == 3
    assert {lv["confidence"]: lv["accepted"] for lv in cal["levels"]} == {9: 0, 6: 1, 5: 0}

    # Mapped again to other concepts: the pick is decided again against the new mapping
    map_to("man one", 201, replace=True)
    assert _status("man one") == [("rejected", "below_threshold")]

    # Undo: the picks wait again and leave the calibration
    resp = client.post(
        "/api/mappings/unmap",
        json={"source_ids": [ids["man two"], ids["man three"]]},
        headers=user_headers,
    )
    assert resp.status_code == 200, resp.text
    assert _status("man two") == [("below_threshold", None)]
    assert _status("man three") == [("pending", None)]
    assert calibration()["decided"] == 1

    # Two pending picks decided by hand (an auto-map ran again in between): Undo brings back only
    # the newest, as there is one pending suggestion per term
    def add_pending(confidence):
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mapping_suggestion (source_id, concept_id, confidence, method)"
                    " VALUES (:s, 203, :c, 'auto_standard')"
                ),
                {"s": ids["man four"], "c": confidence},
            )

    add_pending(7)
    map_to("man four", 203)
    add_pending(8)
    map_to("man four", 201, replace=True)
    assert sorted(_status("man four")) == [("rejected", "pending"), ("rejected", "pending")]
    resp = client.post(
        "/api/mappings/unmap", json={"source_ids": [ids["man four"]]}, headers=user_headers
    )
    assert resp.status_code == 200, resp.text
    assert _status("man four") == [("pending", None)]
    with get_engine().connect() as conn:
        assert (
            conn.execute(
                text("SELECT confidence FROM mapping_suggestion WHERE source_id = :s"),
                {"s": ids["man four"]},
            ).scalar_one()
            == 8
        )
