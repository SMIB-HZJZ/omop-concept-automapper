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


def test_calibration_recommends_a_threshold(client, user_headers, loaded_vocabulary):
    import_source_csv(client, user_headers, 70, CSV)
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


def test_priority_sort_puts_frequent_uncertain_terms_first(client, user_headers, loaded_vocabulary):
    import_source_csv(client, user_headers, 71, CSV.replace(b"cal", b"pri"))
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
