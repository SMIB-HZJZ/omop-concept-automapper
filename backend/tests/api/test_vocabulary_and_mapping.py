from tests.conftest import import_source_csv


def test_vocabulary_status_lists_files(client, user_headers):
    status = client.get("/api/vocabulary/status", headers=user_headers).json()
    files = {f["table_name"]: f for f in status["files"]}
    assert files["concept"]["exists"] and files["concept"]["size"] > 0


def test_vocabulary_import_and_atc7(client, admin_headers, loaded_vocabulary):
    status = client.get("/api/vocabulary/status", headers=admin_headers).json()
    counts = {t["table_name"]: t["row_count"] for t in status["tables"]}
    assert counts == {
        "concept": 25,
        "concept_relationship": 4,
        "concept_ancestor": 12,
        "drug_strength": 0,  # optional file, absent in the fixtures
    }
    assert all(t["last_import"] for t in status["tables"] if t["table_name"] != "drug_strength")

    tasks = client.get(
        "/api/tasks", params={"kind": "vocabulary.import"}, headers=admin_headers
    ).json()
    result = tasks[0]["result"]
    assert result["tables_imported"] == 3
    assert tasks[0]["percentage"] == 100.0

    stats = client.get("/api/atc7/stats", headers=admin_headers).json()
    # metformin x5 (incl. box/marketed), acetaminophen, ibuprofen x2, amoxicillin; the 5-char code is ignored
    assert stats["total_drugs_with_atc7"] == 9
    assert stats["avg_atc7_per_drug"] == 1.0
    concept = client.get("/api/concepts/1", headers=admin_headers).json()
    assert concept["atc7_codes"] == ["A10BA02"]


def test_reimport_upserts_concepts(client, admin_headers, loaded_vocabulary):
    resp = client.post("/api/vocabulary/import", headers=admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "SUCCESS"
    assert task["result"]["tables"]["concept"]["records_imported"] == 0  # net new rows


def test_concept_lookup(client, user_headers, loaded_vocabulary):
    resp = client.get("/api/concepts", params={"ids": "201; 4,123456"}, headers=user_headers).json()
    assert [c["concept_id"] for c in resp["found"]] == [201, 4]
    assert resp["missing"] == [123456]
    assert (
        client.get("/api/concepts", params={"ids": "abc"}, headers=user_headers).status_code == 400
    )
    assert client.get("/api/concepts/999999", headers=user_headers).status_code == 404


def test_source_import_validates_columns(client, user_headers):
    resp = client.post(
        "/api/source-vocabularies/7/concepts/import",
        files={"file": ("bad.csv", b"code,name\n1,x\n", "text/csv")},
        headers=user_headers,
    )
    assert resp.status_code == 400
    assert "source_value" in resp.json()["detail"]


def test_source_import_and_mapping_flow(client, user_headers, admin_headers, loaded_vocabulary):
    task = import_source_csv(client, user_headers, 3, name="Hospital drugs", is_drug="true")
    assert task["status"] == "SUCCESS", task
    result = task["result"]
    assert result["inserted"] == 6
    assert result["premapped_sources"] == 2  # paracetamol -> 3, ibuprofen -> 4
    assert result["invalid_concept_ids"] == [999999]

    vocabs = {
        v["id"]: v for v in client.get("/api/source-vocabularies", headers=user_headers).json()
    }
    assert vocabs[3]["name"] == "Hospital drugs" and vocabs[3]["is_drug"] is True
    assert vocabs[3]["concept_count"] == 6 and vocabs[3]["mapped_count"] == 2

    page = client.get(
        "/api/source-vocabularies/3/unmapped", params={"per_page": 2}, headers=user_headers
    ).json()
    assert page["total"] == 4 and page["pages"] == 2
    first = page["items"][0]
    assert first["source_concept_name"] == "metformin 500 mg tableta"  # ordered by freq desc
    assert first["atc7_codes"] == ["A10BA02"]
    page2 = client.get(
        "/api/source-vocabularies/3/unmapped",
        params={"per_page": 2, "page": 2},
        headers=user_headers,
    ).json()
    values = [i["source_value"] for i in page2["items"]]
    assert "00042" in values  # leading zeros are preserved

    # Manual mapping replaces existing mappings and writes a 'manual' audit row
    source_id = first["source_id"]
    assert (
        client.post(
            "/api/mappings",
            json={"source_id": source_id, "concept_ids": [1, 999]},
            headers=user_headers,
        ).status_code
        == 400
    )
    resp = client.post(
        "/api/mappings", json={"source_id": source_id, "concept_ids": [1, 2]}, headers=user_headers
    )
    assert resp.json()["count"] == 2
    resp = client.post(
        "/api/mappings", json={"source_id": source_id, "concept_ids": [1]}, headers=user_headers
    )
    page = client.get("/api/source-vocabularies/3/mapped", headers=user_headers).json()
    assert page["total"] == len(page["items"]) and page["pages"] == 1
    mapped = page["items"]
    filtered = client.get(
        "/api/source-vocabularies/3/mapped", params={"q": "paracetamol"}, headers=user_headers
    ).json()
    assert filtered["total"] >= 1
    assert all(
        "paracetamol" in r["source_concept_name"].lower()
        or "paracetamol" in r["concept_name"].lower()
        for r in filtered["items"]
    )
    assert sorted(r["concept_id"] for r in mapped if r["source_id"] == source_id) == [1]
    assert next(r for r in mapped if r["source_id"] == source_id)["mapped_by"] == "mapper"

    stats = client.get("/api/source-vocabularies/3/stats", headers=user_headers).json()
    assert stats["total"] == 6 and stats["mapped"] == 3 and stats["unmapped"] == 3
    assert stats["total_mappings"] == 3 and stats["unique_concepts"] == 3
    assert stats["frequency_coverage"] == 120 + 80 + 1
    assert {d["domain_id"]: d["count"] for d in stats["by_domain"]} == {"Drug": 3}

    audit = client.get("/api/source-vocabularies/3/audit/stats", headers=user_headers).json()
    assert audit[0]["mapping_method"] == "manual" and audit[0]["mapping_count"] == 3
    recent = client.get("/api/source-vocabularies/3/audit/recent", headers=user_headers).json()
    assert recent[0]["created_by"] == "mapper"

    csv_resp = client.get("/api/source-vocabularies/3/mapped.csv", headers=user_headers)
    assert csv_resp.headers["content-disposition"].endswith('filename="vocabulary_3_mappings.csv"')
    lines = csv_resp.text.strip().splitlines()
    assert (
        lines[0]
        == "source_id,source_value,source_concept_name,concept_name,concept_id,domain_id,freq"
    )
    assert len(lines) == 4

    resp = client.post(
        "/api/mappings/unmap", json={"source_ids": [source_id]}, headers=user_headers
    )
    assert resp.json()["count"] == 1
    assert (
        client.get("/api/source-vocabularies/3/stats", headers=user_headers).json()["mapped"] == 2
    )


def test_delete_vocabulary(client, user_headers, admin_headers, loaded_vocabulary):
    import_source_csv(client, user_headers, 9)
    resp = client.delete("/api/source-vocabularies/9", headers=admin_headers)
    assert resp.json()["count"] == 6
    ids = [v["id"] for v in client.get("/api/source-vocabularies", headers=user_headers).json()]
    assert 9 not in ids
    assert client.get("/api/source-vocabularies/9/stats", headers=user_headers).status_code == 404


def test_vocabulary_crud(client, user_headers):
    resp = client.post(
        "/api/source-vocabularies", json={"id": 42, "name": "Labs"}, headers=user_headers
    )
    assert resp.status_code == 201
    assert (
        client.post("/api/source-vocabularies", json={"id": 42}, headers=user_headers).status_code
        == 409
    )
    resp = client.patch(
        "/api/source-vocabularies/42",
        json={"is_drug": True, "description": "lab codes"},
        headers=user_headers,
    )
    assert resp.json()["is_drug"] is True and resp.json()["description"] == "lab codes"


def test_clean_non_standard_mappings_after_reimport(
    client, user_headers, admin_headers, loaded_vocabulary
):
    import_source_csv(
        client,
        user_headers,
        11,
        content=b"source_value,source_concept_name,concept_id\nE11,dm2,501\nE11b,dm2 b,201\n",
    )
    stats = client.get("/api/source-vocabularies/11/stats", headers=user_headers).json()
    assert stats["mapped"] == 2  # pre-mapping to a non-standard concept is accepted on import
    resp = client.post("/api/vocabulary/import", headers=admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["result"]["non_standard_mappings_removed"] >= 1
    assert (
        client.get("/api/source-vocabularies/11/stats", headers=user_headers).json()["mapped"] == 1
    )
