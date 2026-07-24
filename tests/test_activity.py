"""Tests für Aktivitäts-Feed, Gesehen-Markierung und Ungelesen-Punkte."""

from __future__ import annotations

import uuid


def _setup_shared_entry(client, second_client):
    r = client.post("/api/sources", json={"label": "Team", "kind": "network"})
    sid = r.json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "doc.txt", "name": "doc.txt"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    entry = client.get(f"/api/sources/{sid}/entries").json()[0]
    me2 = second_client.get("/api/auth/me").json()
    r = client.post(
        f"/api/sources/{sid}/shares",
        json={"identifier": me2["email"], "permission": "annotate"},
    )
    assert r.status_code == 201
    return sid, entry["entry_id"], me2


def test_feed_contains_foreign_annotations_and_scans(client, second_client):
    _sid, eid, _me2 = _setup_shared_entry(client, second_client)
    r = second_client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "vom Kollegen"})
    assert r.status_code == 201

    feed = client.get("/api/activity").json()
    kinds = {(i["kind"]) for i in feed["items"]}
    assert "annotation" in kinds
    assert "scan" in kinds
    note = next(i for i in feed["items"] if i["kind"] == "annotation")
    assert note["author_name"] == "Kollege"
    assert note["is_own"] is False
    assert note["entry_name"] == "doc.txt"


def test_activity_badge_resets_after_seen(client, second_client):
    _sid, eid, _me2 = _setup_shared_entry(client, second_client)
    second_client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "neu"})

    n = client.get("/api/notifications").json()
    assert n["activity_new"] >= 1

    client.post("/api/activity/seen")
    n = client.get("/api/notifications").json()
    assert n["activity_new"] == 0

    # Neue fremde Aktivität hebt das Badge wieder an.
    second_client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "noch eine"})
    n = client.get("/api/notifications").json()
    assert n["activity_new"] == 1


def test_own_annotations_do_not_count_as_new(client):
    r = client.post("/api/sources", json={"label": "Solo", "kind": "local"})
    sid = r.json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "x.txt", "name": "x.txt"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    eid = client.get(f"/api/sources/{sid}/entries").json()[0]["entry_id"]
    client.post("/api/annotations", json={"entry_id": eid, "type": "note", "body": "meins"})

    hits = client.get(f"/api/sources/{sid}/entries").json()
    assert hits[0]["has_new"] is False


def test_unread_dot_until_source_seen(client, second_client):
    sid, eid, _me2 = _setup_shared_entry(client, second_client)
    second_client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "sichtbar?"})

    # Fremde Notiz + Quelle nie besucht -> Punkt.
    hits = client.get(f"/api/sources/{sid}/entries").json()
    assert hits[0]["has_new"] is True

    # Besuch markieren -> Punkt verschwindet.
    r = client.post(f"/api/sources/{sid}/seen")
    assert r.status_code == 200
    hits = client.get(f"/api/sources/{sid}/entries").json()
    assert hits[0]["has_new"] is False
