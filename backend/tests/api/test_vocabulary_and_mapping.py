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
        "concept_synonym": 4,
    }
    assert all(t["last_import"] for t in status["tables"] if t["table_name"] != "drug_strength")

    tasks = client.get(
        "/api/tasks", params={"kind": "vocabulary.import"}, headers=admin_headers
    ).json()
    result = tasks[0]["result"]
    assert result["tables_imported"] == 4
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
    resp = client.post("/api/vocabulary/import", headers=admin_headers)
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


def test_vocabulary_import_reads_an_athena_zip(
    client, admin_headers, loaded_vocabulary, tmp_path, monkeypatch
):
    import zipfile

    from app.core.config import get_settings
    from tests.conftest import FIXTURES

    archive = tmp_path / "vocabulary_download_v5.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for csv in (FIXTURES / "vocabulary").glob("*.csv"):
            zf.write(csv, f"athena/{csv.name}")  # a sub-folder inside the zip is fine
        zf.writestr("readme.txt", "not a table")
    monkeypatch.setattr(get_settings(), "vocabulary_dir", str(tmp_path))

    files = client.get("/api/vocabulary/status", headers=admin_headers).json()["files"]
    by_table = {f["table_name"]: f for f in files}
    assert by_table["concept"]["exists"] and by_table["concept"]["archive"] == archive.name
    assert by_table["drug_strength"]["exists"] is False  # not in the fixtures, so not in the zip

    resp = client.post("/api/vocabulary/import", headers=admin_headers)
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "SUCCESS", task
    assert task["result"]["tables"]["concept_relationship"]["status"] == "success"
    assert task["result"]["tables"]["concept_relationship"]["records_imported"] > 0


def test_newer_csv_wins_over_an_older_zip(tmp_path):
    import os
    import zipfile

    from app.services.vocabulary_status import check_vocabulary_files

    archive = tmp_path / "old.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("CONCEPT.csv", "concept_id\n")
        zf.writestr("CONCEPT_ANCESTOR.csv", "ancestor_concept_id\n")
    os.utime(archive, (1_000_000, 1_000_000))
    (tmp_path / "CONCEPT.csv").write_text("concept_id\n")
    files = check_vocabulary_files(str(tmp_path))
    assert files["concept"]["archive"] is None  # the CSV is newer
    assert files["concept_ancestor"]["archive"] == "old.zip"  # only in the zip
    assert files["concept_relationship"]["exists"] is False


def test_vocabulary_update_reembeds_changed_and_new_concepts(
    client, admin_headers, embedded, tmp_path, monkeypatch
):
    """A new release: a renamed concept is re-embedded, a new concept in an embedded domain is
    embedded, a new concept in a domain without vectors is not; ATC7 is recomputed."""
    import shutil

    from sqlalchemy import text

    from app.core.config import get_settings
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

    def run_import():
        resp = client.post("/api/vocabulary/import", headers=admin_headers)
        task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
        assert task["status"] == "SUCCESS", task
        return task["result"]

    monkeypatch.setattr(get_settings(), "vocabulary_dir", str(tmp_path))
    try:
        EMBED_CALLS.clear()
        result = run_import()
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
        monkeypatch.undo()
        with session_scope() as session:
            session.execute(text("DELETE FROM concept WHERE concept_id IN (9001, 9002)"))
            session.commit()
        restored = run_import()  # back to the fixture names (and their vectors)
        assert restored["embedding_refresh"]["changed"] == 1
