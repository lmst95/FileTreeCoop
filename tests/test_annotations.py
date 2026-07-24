"""Tests für Annotationen: Notizen/Todos/Labels/Übergaben + Zugriffsschutz."""

from __future__ import annotations

import uuid


def _source_with_entry(client):
    sid = client.post("/api/sources", json={"label": "Q", "kind": "local"}).json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "f.txt", "name": "f.txt"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    entry = client.get(f"/api/sources/{sid}/entries").json()[0]
    return sid, entry["entry_id"]


def test_crud_note(client):
    _, eid = _source_with_entry(client)
    r = client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "erste Notiz"})
    assert r.status_code == 201
    ann_id = r.json()["id"]

    r = client.patch(f"/api/annotations/{ann_id}", json={"body": "geändert"})
    assert r.status_code == 200
    assert r.json()["body"] == "geändert"

    r = client.get(f"/api/annotations/by-entry/{eid}")
    assert len(r.json()) == 1

    assert client.delete(f"/api/annotations/{ann_id}").status_code == 204
    assert client.get(f"/api/annotations/by-entry/{eid}").json() == []


def test_todo_toggle_done(client):
    _, eid = _source_with_entry(client)
    ann = client.post("/api/annotations", json={
        "entry_id": eid, "type": "todo", "body": "erledigen"}).json()
    assert ann["done"] is False
    updated = client.patch(f"/api/annotations/{ann['id']}", json={"done": True}).json()
    assert updated["done"] is True


def test_invalid_type_rejected(client):
    _, eid = _source_with_entry(client)
    r = client.post("/api/annotations", json={"entry_id": eid, "type": "bogus"})
    assert r.status_code == 422


def test_no_access_to_foreign_entry(client, second_client):
    _, eid = _source_with_entry(client)
    # second_client hat keinen Zugriff auf die Quelle von client.
    r = second_client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "hack"})
    assert r.status_code == 403


def test_requires_auth(client):
    from fastapi.testclient import TestClient
    from app.main import app
    anon = TestClient(app)  # nicht eingeloggt
    assert anon.get("/api/sources").status_code == 401
