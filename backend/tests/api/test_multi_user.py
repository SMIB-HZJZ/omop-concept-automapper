"""Several people mapping one vocabulary: a mapping made meanwhile is not overwritten silently,
and the term searches match case-insensitively with literal % and _."""

from tests.conftest import import_source_csv


def _terms(client, headers, vid: int) -> dict[str, int]:
    page = client.get(f"/api/source-vocabularies/{vid}/unmapped", headers=headers).json()
    return {i["source_value"]: i["source_id"] for i in page["items"]}


def test_mapping_made_meanwhile_is_not_overwritten(
    client, admin_headers, user_headers, loaded_vocabulary
):
    vid = 140
    import_source_csv(client, admin_headers, vid, b"source_value,source_concept_name\nA,dm2\n")
    term = _terms(client, admin_headers, vid)["A"]
    # A colleague maps the term first
    first = client.post(
        "/api/mappings", json={"source_id": term, "concept_ids": [201]}, headers=user_headers
    )
    assert first.status_code == 200
    # The same choice again (e.g. a double click from a stale screen) is fine and changes nothing
    again = client.post(
        "/api/mappings", json={"source_id": term, "concept_ids": [201]}, headers=admin_headers
    )
    assert again.status_code == 200
    # A different choice from a stale screen is refused, naming who mapped it
    other = client.post(
        "/api/mappings", json={"source_id": term, "concept_ids": [202]}, headers=admin_headers
    )
    assert other.status_code == 409
    detail = other.json()["detail"]
    assert detail["code"] == "already_mapped"
    assert detail["mapped_by"] == "mapper" and detail["concept_ids"] == [201]
    # ... unless the user confirms the replacement
    replaced = client.post(
        "/api/mappings",
        json={"source_id": term, "concept_ids": [202], "replace": True},
        headers=admin_headers,
    )
    assert replaced.status_code == 200
    mapped = client.get(f"/api/source-vocabularies/{vid}/mapped", headers=admin_headers).json()
    assert [r["concept_id"] for r in mapped["items"]] == [202]


def test_term_searches(client, admin_headers, loaded_vocabulary):
    vid = 141
    import_source_csv(
        client,
        admin_headers,
        vid,
        b"source_value,source_concept_name,concept_id\n"
        b"LAB_1,Glukoza 100%,\nLAB21,Glukoza natasce,\nDG-1,Dijabetes,201\n",
    )
    unmapped = client.get(
        f"/api/source-vocabularies/{vid}/unmapped", params={"q": "GLUKOZA"}, headers=admin_headers
    ).json()
    assert {i["source_value"] for i in unmapped["items"]} == {"LAB_1", "LAB21"}
    # "_" and "%" are literal characters, not wildcards
    for q, expected in (("lab_", {"LAB_1"}), ("100%", {"LAB_1"})):
        found = client.get(
            f"/api/source-vocabularies/{vid}/unmapped", params={"q": q}, headers=admin_headers
        ).json()
        assert {i["source_value"] for i in found["items"]} == expected, q
    for q in ("dijab", "TYPE 2 DIABETES", "201"):
        mapped = client.get(
            f"/api/source-vocabularies/{vid}/mapped", params={"q": q}, headers=admin_headers
        ).json()
        assert [r["source_value"] for r in mapped["items"]] == ["DG-1"], q
