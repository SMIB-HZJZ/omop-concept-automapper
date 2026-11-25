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


def test_combined_export_keeps_history_and_takes_an_older_release(
    client, admin_headers, user_headers, loaded_vocabulary
):
    vid = 187
    _vocabulary(client, admin_headers, vid, "Labs", b"A,alpha,201\nB,beta,202\n")
    _vocabulary(client, admin_headers, 188, "Other", b"X,x,201\n")
    # Release 2: A remapped, B removed
    assert (
        client.post(
            f"/api/source-vocabularies/{vid}/releases/next", headers=admin_headers
        ).status_code
        == 200
    )
    resp = client.get(
        f"/api/source-vocabularies/{vid}/unmapped", params={"view": "all"}, headers=admin_headers
    )
    ids = {r["source_value"]: r["source_id"] for r in resp.json()["items"]}
    mapped = client.get(f"/api/source-vocabularies/{vid}/mapped", headers=admin_headers).json()[
        "items"
    ]
    ids.update({r["source_value"]: r["source_id"] for r in mapped})
    remap = client.post(
        "/api/mappings",
        json={"source_id": ids["A"], "concept_ids": [202], "replace": True},
        headers=admin_headers,
    )
    assert remap.status_code == 200, remap.text
    unmap = client.post(
        "/api/mappings/unmap", json={"source_ids": [ids["B"]]}, headers=admin_headers
    )
    assert unmap.status_code == 200, unmap.text
    assert (
        client.post(
            f"/api/source-vocabularies/{vid}/releases", json={}, headers=admin_headers
        ).status_code
        == 201
    )

    def rows(*items):
        resp = client.get(URL, params={"vocabulary": list(items)}, headers=user_headers)
        assert resp.status_code == 200, resp.text
        return [
            (
                r["source_vocabulary_id"],
                r["source_code"],
                int(r["target_concept_id"]),
                r["invalid_reason"],
            )
            for r in csv.DictReader(io.StringIO(resp.text))
        ]

    assert rows("187::LAB", "188::OTH") == [
        ("LAB", "A", 201, "U"),
        ("LAB", "A", 202, ""),
        ("LAB", "B", 202, "D"),
        ("OTH", "X", 201, ""),
    ]
    # Release 1 as it was published, under the same label
    assert rows("187:1:LAB") == [("LAB", "A", 201, ""), ("LAB", "B", 202, "")]
