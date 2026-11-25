"""One SOURCE_TO_CONCEPT_MAP for several published vocabularies."""

import csv
import io

from tests.conftest import import_source_csv

URL = "/api/source_to_concept_map.csv"


def _vocabulary(client, admin_headers, vid: int, name: str, rows: bytes, publish: bool = True):
    import_source_csv(
        client,
        admin_headers,
        vid,
        content=b"source_value,source_concept_name,concept_id\n" + rows,
        name=name,
    )
    if publish:
        resp = client.post(
            f"/api/source-vocabularies/{vid}/releases", json={}, headers=admin_headers
        )
        assert resp.status_code == 201, resp.text


def _rows(resp) -> list[tuple]:
    assert resp.status_code == 200, resp.text
    return [
        (r["source_vocabulary_id"], r["source_code"], int(r["target_concept_id"]))
        for r in csv.DictReader(io.StringIO(resp.text))
    ]


def test_combined_export_of_published_vocabularies(
    client, admin_headers, user_headers, loaded_vocabulary
):
    _vocabulary(client, admin_headers, 181, "Lab tests", b"A,alpha,201\nB,beta,202\n")
    _vocabulary(client, admin_headers, 182, "Diagnoses (GP)", b"A,alpha,201\n")
    _vocabulary(client, admin_headers, 183, "Draft only", b"X,x,201\n", publish=False)

    resp = client.get(URL, params={"vocabulary": ["181", "182"]}, headers=user_headers)
    assert _rows(resp) == [
        ("Lab_tests", "A", 201),
        ("Lab_tests", "B", 202),
        ("Diagnoses_GP", "A", 201),
    ]
    assert "source_to_concept_map.csv" in resp.headers["content-disposition"]

    # Own labels, an explicit release and the order given
    resp = client.get(
        URL, params={"vocabulary": ["182:1:HZJZ_DG", "181::HZJZ_LAB"]}, headers=user_headers
    )
    assert [r[0] for r in _rows(resp)] == ["HZJZ_DG", "HZJZ_LAB", "HZJZ_LAB"]


def test_combined_export_refuses_what_it_cannot_export(
    client, admin_headers, user_headers, loaded_vocabulary
):
    _vocabulary(client, admin_headers, 184, "Procedures", b"A,alpha,201\n")
    _vocabulary(client, admin_headers, 185, "Unreleased", b"A,alpha,201\n", publish=False)
    _vocabulary(client, admin_headers, 186, "Procedures!", b"B,beta,202\n")

    def status(*items):
        return client.get(URL, params={"vocabulary": list(items)}, headers=user_headers).status_code

    assert status("184", "185") == 409  # not published
    assert status("184:7") == 404  # no such release
    assert status("999999") == 404
    assert status("184", "184") == 400  # twice
    assert status("184::SAME", "186:1:SAME") == 400
    assert status("184", "186") == 400  # both named "Procedures" by default
    assert status("184", "186::PROC2") == 200
    assert status("abc") == 400
    assert client.get(URL, headers=user_headers).status_code == 422
