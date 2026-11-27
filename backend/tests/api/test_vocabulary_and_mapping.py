from tests.conftest import import_source_csv, upload_vocabulary, vocabulary_zip


def test_vocabulary_upload_is_checked(client, admin_headers, user_headers):
    import io
    import os
    import zipfile

    from app.core.config import get_settings

    assert upload_vocabulary(client, user_headers).status_code == 403
    bad = upload_vocabulary(client, admin_headers, b"concept_id\tconcept_name\n")
    assert bad.status_code == 400 and "not a valid zip" in bad.json()["detail"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no tables")
    empty = upload_vocabulary(client, admin_headers, buf.getvalue())
    assert empty.status_code == 400 and "CONCEPT.csv" in empty.json()["detail"]
    assert upload_vocabulary(client, admin_headers, b"").status_code == 400
    # Rejected uploads are not left on disk
    upload_dir = get_settings().upload_dir
    assert not [f for f in os.listdir(upload_dir) if f.startswith("vocabulary-")]
    status = client.get("/api/vocabulary/status", headers=user_headers).json()
    assert status["max_upload_bytes"] > 1024**3
    assert len(vocabulary_zip()) > 0


def test_vocabulary_upload_size_limit(client, admin_headers, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "vocabulary_upload_max_mb", 0)
    resp = upload_vocabulary(client, admin_headers)
    assert resp.status_code == 413


def test_vocabulary_import_and_atc7(client, admin_headers, loaded_vocabulary):
    status = client.get("/api/vocabulary/status", headers=admin_headers).json()
    counts = {t["table_name"]: t["row_count"] for t in status["tables"]}
    assert counts == {
        "concept": 25,
        "concept_relationship": 4,
        "concept_ancestor": 12,
        "drug_strength": 0,  # optional file, absent in the fixtures
        "concept_synonym": 4,
        "vocabulary": 4,
    }
    assert all(t["last_import"] for t in status["tables"] if t["table_name"] != "drug_strength")
    # The release version is the 'None' row of VOCABULARY.csv
    assert status["vocabulary_version"] == "v5.0 27-AUG-25"

    tasks = client.get(
        "/api/tasks", params={"kind": "vocabulary.import"}, headers=admin_headers
    ).json()
    result = tasks[0]["result"]
    assert result["tables_imported"] == 5
    assert tasks[0]["percentage"] == 100.0

    stats = client.get("/api/atc7/stats", headers=admin_headers).json()
    # metformin x5 (incl. box/marketed), acetaminophen, ibuprofen x2, amoxicillin; the 5-char code is ignored
    assert stats["total_drugs_with_atc7"] == 9
    assert stats["avg_atc7_per_drug"] == 1.0
    concept = client.get("/api/concepts/1", headers=admin_headers).json()
    assert concept["atc7_codes"] == ["A10BA02"]


def test_reimport_upserts_concepts(client, admin_headers, loaded_vocabulary):
    resp = upload_vocabulary(client, admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "SUCCESS"
    concept = task["result"]["tables"]["concept"]
    assert concept["records_imported"] == 0 and concept["records_updated"] == 0  # nothing changed


def test_concept_lookup(client, user_headers, loaded_vocabulary):
    resp = client.get("/api/concepts", params={"ids": "201; 4,123456"}, headers=user_headers).json()
    assert [c["concept_id"] for c in resp["found"]] == [201, 4]
    assert resp["missing"] == [123456]
    assert (
        client.get("/api/concepts", params={"ids": "abc"}, headers=user_headers).status_code == 400
    )
    assert client.get("/api/concepts/999999", headers=user_headers).status_code == 404


def test_source_import_validates_columns(client, admin_headers, user_headers):
    resp = client.post(
        "/api/source-vocabularies/7/concepts/import",
        files={"file": ("bad.csv", b"code,name\n1,x\n", "text/csv")},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "source_value" in resp.json()["detail"]


def test_source_import_and_mapping_flow(client, user_headers, admin_headers, loaded_vocabulary):
    task = import_source_csv(client, admin_headers, 3, name="Hospital drugs", is_drug="true")
    assert task["status"] == "SUCCESS", task
    result = task["result"]
    assert result["inserted"] == 6
    assert result["premapped_sources"] == 2  # paracetamol -> 3, ibuprofen -> 4
    assert result["invalid_concept_ids"] == [999999]

    vocabs = {
        v["id"]: v for v in client.get("/api/source-vocabularies", headers=admin_headers).json()
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
    page = client.get("/api/source-vocabularies/3/mapped", headers=admin_headers).json()
    assert page["total"] == len(page["items"]) and page["pages"] == 1
    mapped = page["items"]
    filtered = client.get(
        "/api/source-vocabularies/3/mapped", params={"q": "paracetamol"}, headers=admin_headers
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

    csv_resp = client.get("/api/source-vocabularies/3/mapped.csv", headers=admin_headers)
    assert csv_resp.headers["content-disposition"].endswith('filename="vocabulary_3_mappings.csv"')
    lines = csv_resp.text.strip().splitlines()
    assert (
        lines[0]
        == "source_id,source_value,source_concept_name,concept_name,concept_id,domain_id,freq,"
        "match_type"
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
    import_source_csv(client, admin_headers, 9)
    resp = client.delete("/api/source-vocabularies/9", headers=admin_headers)
    assert resp.json()["count"] == 6
    ids = [v["id"] for v in client.get("/api/source-vocabularies", headers=admin_headers).json()]
    assert 9 not in ids
    assert client.get("/api/source-vocabularies/9/stats", headers=user_headers).status_code == 404


def test_vocabulary_crud(client, admin_headers, user_headers):
    resp = client.post(
        "/api/source-vocabularies", json={"id": 42, "name": "Labs"}, headers=admin_headers
    )
    assert resp.status_code == 201
    assert (
        client.post(
            "/api/source-vocabularies", json={"id": 42, "name": "Labs"}, headers=admin_headers
        ).status_code
        == 409
    )
    resp = client.patch(
        "/api/source-vocabularies/42",
        json={"is_drug": True, "description": "lab codes"},
        headers=admin_headers,
    )
    assert resp.json()["is_drug"] is True and resp.json()["description"] == "lab codes"


def test_non_standard_mappings_are_replaced_or_removed_after_reimport(
    client, user_headers, admin_headers, loaded_vocabulary
):
    import_source_csv(
        client,
        admin_headers,
        11,
        content=(
            b"source_value,source_concept_name,concept_id\n"
            b"E11,dm2,501\nX1,metformin class,101\nE11b,dm2 b,201\n"
        ),
    )
    stats = client.get("/api/source-vocabularies/11/stats", headers=user_headers).json()
    assert stats["mapped"] == 3  # pre-mapping to a non-standard concept is accepted on import
    resp = upload_vocabulary(client, admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    # ICD10 501 has "Maps to" SNOMED 201 and is replaced; ATC class 101 has no successor
    assert task["result"]["non_standard_mappings_replaced"] == 1
    assert task["result"]["non_standard_mappings_removed"] == 1
    assert (
        client.get("/api/source-vocabularies/11/stats", headers=user_headers).json()["mapped"] == 2
    )
    audit = client.get("/api/source-vocabularies/11/audit/stats", headers=user_headers)
    assert audit.status_code == 200
    methods = {r["mapping_method"] for r in audit.json()}
    assert {"auto_vocab_update", "vocab_removed"} <= methods


def test_vocabulary_import_reads_an_athena_zip(client, admin_headers, loaded_vocabulary):
    import os

    from app.core.config import get_settings

    # A sub-folder inside the zip is fine; drug_strength is not in the fixtures
    resp = upload_vocabulary(client, admin_headers, vocabulary_zip(prefix="athena/"))
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "SUCCESS", task
    tables = task["result"]["tables"]
    assert tables["concept_relationship"]["status"] == "success"
    assert tables["concept_relationship"]["records_imported"] > 0
    assert tables["drug_strength"]["status"] == "skipped"
    # The uploaded zip is deleted after the import
    upload_dir = get_settings().upload_dir
    assert not [f for f in os.listdir(upload_dir) if f.startswith("vocabulary-")]


def test_vocabulary_update_reembeds_changed_and_new_concepts(
    client, admin_headers, embedded, tmp_path, monkeypatch
):
    """A new release: a renamed concept is re-embedded, a new concept in an embedded domain is
    embedded, a new concept in a domain without vectors is not; ATC7 is recomputed."""
    import shutil

    from sqlalchemy import text

    from app.core.db import session_scope
    from tests.conftest import EMBED_CALLS, FIXTURES

    for csv in (FIXTURES / "vocabulary").glob("*.csv"):
        shutil.copy(csv, tmp_path / csv.name)
    concept = tmp_path / "CONCEPT.csv"
    rows = concept.read_text().splitlines()
    rows = [
        r.replace("201\tType 2 diabetes mellitus\t", "201\tType 2 diabetes mellitus, renamed\t")
        for r in rows
    ]
    rows += [
        "9001\tNewly released condition\tCondition\tSNOMED\tClinical Finding\tS\t9001\t19700101\t20991231\t",
        "9002\tNewly released specimen\tSpecimen\tSNOMED\tSpecimen\tS\t9002\t19700101\t20991231\t",
    ]
    concept.write_text("\n".join(rows) + "\n")

    def run_import(source=None):
        resp = upload_vocabulary(client, admin_headers, vocabulary_zip(source))
        task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
        assert task["status"] == "SUCCESS", task
        return task["result"]

    try:
        EMBED_CALLS.clear()
        result = run_import(tmp_path)
        assert result["vectors_text_changed"] == 1
        assert result["drugs_with_atc7"] is not None
        refresh = result["embedding_refresh"]
        assert (refresh["changed"], refresh["new"]) == (1, 1), refresh
        assert refresh["task_id"]
        refresh_task = client.get(f"/api/tasks/{refresh['task_id']}", headers=admin_headers).json()
        assert refresh_task["status"] == "SUCCESS", refresh_task
        texts = [t for call in EMBED_CALLS for t in call]
        assert sorted(texts) == ["Newly released condition", "Type 2 diabetes mellitus, renamed"]
        status = client.get("/api/embeddings/status", headers=admin_headers).json()
        assert status["changed"] == 0
    finally:
        with session_scope() as session:
            session.execute(text("DELETE FROM concept WHERE concept_id IN (9001, 9002)"))
            session.commit()
        restored = run_import()  # back to the fixture names (and their vectors)
        assert restored["embedding_refresh"]["changed"] == 1
