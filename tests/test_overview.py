"""Tests: mehrere Annotationen pro Datei + Übersichts-Endpunkt mit Filtern."""

from __future__ import annotations

import uuid


def _me(c):
    return c.get("/api/auth/me").json()


def _source_with_entry(client, label="Q"):
    sid = client.post("/api/sources", json={"label": label, "kind": "local"}).json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "f.txt", "name": "f.txt", "ext": "txt"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    eid = client.get(f"/api/sources/{sid}/entries").json()[0]["entry_id"]
    return sid, eid


def _add(client, eid, **kw):
    r = client.post("/api/annotations", json={"entry_id": eid, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_multiple_annotations_per_file(client):
    _, eid = _source_with_entry(client)
    _add(client, eid, type="note", body="erste")
    _add(client, eid, type="note", body="zweite")
    _add(client, eid, type="todo", body="aufräumen")
    _add(client, eid, type="label", label_value="wichtig")

    anns = client.get(f"/api/annotations/by-entry/{eid}").json()
    assert len(anns) == 4
    assert sum(1 for a in anns if a["type"] == "note") == 2


def test_overview_lists_and_enriches(client):
    sid, eid = _source_with_entry(client, label="Projekte")
    _add(client, eid, type="note", body="hallo welt")

    items = client.get("/api/annotations").json()
    assert len(items) == 1
    it = items[0]
    assert it["entry_name"] == "f.txt"
    assert it["entry_path"] == "f.txt"
    assert it["source_id"] == sid
    assert it["source_label"] == "Projekte"


def test_overview_type_filter(client):
    _, eid = _source_with_entry(client)
    _add(client, eid, type="note", body="n")
    _add(client, eid, type="todo", body="t")
    notes = client.get("/api/annotations", params={"type": "note"}).json()
    assert len(notes) == 1 and notes[0]["type"] == "note"


def test_overview_open_todos_and_label_filter(client):
    _, eid = _source_with_entry(client)
    t1 = _add(client, eid, type="todo", body="offen")
    t2 = _add(client, eid, type="todo", body="fertig")
    client.patch(f"/api/annotations/{t2['id']}", json={"done": True})
    _add(client, eid, type="label", label_value="steuer")

    open_todos = client.get("/api/annotations", params={"type": "todo", "done": "false"}).json()
    assert [a["id"] for a in open_todos] == [t1["id"]]

    by_label = client.get("/api/annotations", params={"label": "steuer"}).json()
    assert len(by_label) == 1 and by_label[0]["label_value"] == "steuer"


def test_overview_text_search(client):
    _, eid = _source_with_entry(client)
    _add(client, eid, type="note", body="Angebot Müller")
    _add(client, eid, type="note", body="Rechnung")
    hits = client.get("/api/annotations", params={"q": "müller"}).json()
    assert len(hits) == 1


def test_labels_endpoint_counts(client):
    _, eid = _source_with_entry(client)
    _add(client, eid, type="label", label_value="wichtig")
    _add(client, eid, type="label", label_value="wichtig")
    _add(client, eid, type="label", label_value="später")
    labels = {l["value"]: l["count"] for l in client.get("/api/annotations/labels").json()}
    assert labels == {"wichtig": 2, "später": 1}


def test_overview_assignee_me(client, second_client):
    sid, eid = _source_with_entry(client)
    colleague = _me(second_client)
    client.post(f"/api/sources/{sid}/shares",
                json={"email": colleague["email"], "permission": "annotate"})
    _add(client, eid, type="handover", body="bitte", assignee_user_id=colleague["id"])

    # Aus Sicht des Kollegen: "an mich übergeben".
    mine = second_client.get("/api/annotations", params={"assignee": "me"}).json()
    assert len(mine) == 1 and mine[0]["assignee_name"] == "Kollege"


def test_overview_only_accessible_sources(client, second_client):
    _, eid = _source_with_entry(client)
    _add(client, eid, type="note", body="geheim")
    assert second_client.get("/api/annotations").json() == []
