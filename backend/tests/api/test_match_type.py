"""Manual mappings state their match type (exact unless the mapper says otherwise); mappings,
their CSV export and release copies keep it."""

import csv
import io

from sqlalchemy import text

from app.core.db import session_scope
from tests.conftest import import_source_csv


def test_manual_mapping_match_type(client, admin_headers, user_headers, loaded_vocabulary):
    vid = 130
    import_source_csv(
        client, admin_headers, vid, b"source_value,source_concept_name\nA,dm2\nB,diabetes\n"
    )
    items = client.get(f"/api/source-vocabularies/{vid}/unmapped", headers=admin_headers).json()
    ids = {i["source_value"]: i["source_id"] for i in items["items"]}
    assert (
        client.post(
            "/api/mappings",
            json={"source_id": ids["A"], "concept_ids": [201]},
            headers=user_headers,
        ).status_code
        == 200
    )
    resp = client.post(
        "/api/mappings",
        json={"source_id": ids["B"], "concept_ids": [201], "match_type": "broader"},
        headers=user_headers,
    )
    assert resp.status_code == 200
    bad = client.post(
        "/api/mappings",
        json={"source_id": ids["B"], "concept_ids": [201], "match_type": "close"},
        headers=user_headers,
    )
    assert bad.status_code == 422

    mapped = client.get(f"/api/source-vocabularies/{vid}/mapped", headers=admin_headers).json()
    assert {r["source_value"]: r["match_type"] for r in mapped["items"]} == {
        "A": "exact",
        "B": "broader",
    }
    exported = client.get(f"/api/source-vocabularies/{vid}/mapped.csv", headers=admin_headers)
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert {r["source_value"]: r["match_type"] for r in rows} == {"A": "exact", "B": "broader"}

    # The audit trail and a release copy keep it too
    client.post(f"/api/source-vocabularies/{vid}/releases", json={}, headers=admin_headers)
    with session_scope() as session:
        released = session.execute(
            text(
                "SELECT r.source_value, r.match_type FROM mapping_release_row r "
                "JOIN mapping_release m ON m.id = r.release_id WHERE m.vocabulary_id = :v"
            ),
            {"v": vid},
        ).all()
        audited = session.execute(
            text(
                "SELECT match_type FROM auto_mapping_audit WHERE source_id = :s "
                "AND mapping_method = 'manual'"
            ),
            {"s": ids["B"]},
        ).scalar_one()
    assert dict(released) == {"A": "exact", "B": "broader"} and audited == "broader"
