"""Flagging source terms, the unmapped views and text search, and a term's suggestion."""

from tests.conftest import import_source_csv


def _vocab(client, headers, vid):
    client.post(
        "/api/source-vocabularies", json={"id": vid, "name": f"flags {vid}"}, headers=headers
    )
    import_source_csv(client, headers, vid)


def test_flag_moves_a_term_out_of_the_todo_list(client, admin_headers, loaded_vocabulary):
    _vocab(client, admin_headers, 91)
    url = "/api/source-vocabularies/91/unmapped"
    todo = client.get(url, headers=admin_headers).json()
    assert todo["flagged"] == 0 and todo["total"] > 1
    first = todo["items"][0]

    resp = client.put(
        f"/api/source-concepts/{first['source_id']}/flag",
        json={"flag": "question", "note": "  brand or generic?  "},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    after = client.get(url, headers=admin_headers).json()
    assert after["total"] == todo["total"] - 1 and after["flagged"] == 1
    flagged = client.get(url, params={"view": "flagged"}, headers=admin_headers).json()
    item = flagged["items"][0]
    assert (item["source_id"], item["flag"], item["flag_note"]) == (
        first["source_id"],
        "question",
        "brand or generic?",
    )
    assert item["flagged_by"] and item["flagged_at"]

    client.put(
        f"/api/source-concepts/{first['source_id']}/flag",
        json={"flag": None},
        headers=admin_headers,
    )
    assert client.get(url, headers=admin_headers).json()["total"] == todo["total"]


def test_flag_validation_and_unknown_term(client, admin_headers, loaded_vocabulary):
    assert (
        client.put(
            "/api/source-concepts/99999999/flag", json={"flag": "question"}, headers=admin_headers
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/source-concepts/1/flag", json={"flag": "maybe"}, headers=admin_headers
        ).status_code
        == 422
    )


def test_text_search_in_unmapped(client, admin_headers, loaded_vocabulary):
    _vocab(client, admin_headers, 92)
    url = "/api/source-vocabularies/92/unmapped"
    items = client.get(url, headers=admin_headers).json()["items"]
    word = items[0]["source_concept_name"].split()[0]
    found = client.get(url, params={"q": word.upper()}, headers=admin_headers).json()
    assert found["total"] >= 1
    assert all(
        word.lower() in (i["source_concept_name"] + i["source_value"]).lower()
        for i in found["items"]
    )
    assert (
        client.get(url, params={"q": "zzz-no-such-term"}, headers=admin_headers).json()["total"]
        == 0
    )
