"""Tests für Annotations-Threads (Antworten) und Autoren-Auflösung."""

from __future__ import annotations

import uuid


def _entry(client):
    r = client.post("/api/sources", json={"label": "Laptop", "kind": "local"})
    sid = r.json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "doc.txt", "name": "doc.txt"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    return client.get(f"/api/sources/{sid}/entries").json()[0]["entry_id"]


def test_reply_thread(client):
    eid = _entry(client)
    note = client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "Warum liegt das hier?"}).json()

    r = client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "Altprojekt, kann weg",
        "parent_annotation_id": note["id"]})
    assert r.status_code == 201, r.text
    reply = r.json()
    assert reply["parent_annotation_id"] == note["id"]
    assert reply["author_name"] == "Testnutzer"

    anns = client.get(f"/api/annotations/by-entry/{eid}").json()
    assert len(anns) == 2
    assert {a["parent_annotation_id"] for a in anns} == {None, note["id"]}


def test_reply_to_reply_rejected(client):
    eid = _entry(client)
    note = client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "a"}).json()
    reply = client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "b",
        "parent_annotation_id": note["id"]}).json()
    r = client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "c",
        "parent_annotation_id": reply["id"]})
    assert r.status_code == 422


def test_reply_must_reference_same_entry(client):
    eid1 = _entry(client)
    eid2 = _entry(client)
    note = client.post("/api/annotations", json={
        "entry_id": eid1, "type": "note", "body": "a"}).json()
    r = client.post("/api/annotations", json={
        "entry_id": eid2, "type": "note", "body": "b",
        "parent_annotation_id": note["id"]})
    assert r.status_code == 422


def test_reply_must_be_note(client):
    eid = _entry(client)
    note = client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "a"}).json()
    r = client.post("/api/annotations", json={
        "entry_id": eid, "type": "todo", "body": "b",
        "parent_annotation_id": note["id"]})
    assert r.status_code == 422


def test_deleting_parent_removes_replies(client):
    eid = _entry(client)
    note = client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "a"}).json()
    client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "b",
        "parent_annotation_id": note["id"]})
    r = client.delete(f"/api/annotations/{note['id']}")
    assert r.status_code == 204
    anns = client.get(f"/api/annotations/by-entry/{eid}").json()
    assert anns == []
