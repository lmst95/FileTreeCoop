"""Tests für das Aufräumen verschwundener Einträge."""

from __future__ import annotations

import uuid


def _setup(client):
    sid = client.post("/api/sources", json={"label": "Q", "kind": "local"}).json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [
            {"path": "bleibt.txt", "name": "bleibt.txt"},
            {"path": "weg1.txt", "name": "weg1.txt"},
            {"path": "weg2.txt", "name": "weg2.txt"},
        ], "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    entries = {e["path"]: e for e in client.get(f"/api/sources/{sid}/entries").json()}
    # weg2 bekommt eine Notiz -> muss das Aufräumen überleben.
    client.post("/api/annotations", json={
        "entry_id": entries["weg2.txt"]["entry_id"], "type": "note", "body": "wichtig"})
    # Zweiter Scan ohne die beiden -> missing.
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "bleibt.txt", "name": "bleibt.txt"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    return sid


def test_summary_counts_missing_and_annotated(client):
    sid = _setup(client)
    s = client.get(f"/api/sources/{sid}/missing/summary").json()
    assert s == {"count": 2, "annotated": 1}


def test_cleanup_spares_annotated(client):
    sid = _setup(client)
    r = client.post(f"/api/sources/{sid}/missing/cleanup")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1  # nur weg1 (ohne Notiz)

    paths = sorted(e["path"] for e in client.get(f"/api/sources/{sid}/entries").json())
    assert paths == ["bleibt.txt", "weg2.txt"]


def test_cleanup_include_annotated(client):
    sid = _setup(client)
    r = client.post(f"/api/sources/{sid}/missing/cleanup?include_annotated=true")
    assert r.json()["deleted"] == 2
    paths = [e["path"] for e in client.get(f"/api/sources/{sid}/entries").json()]
    assert paths == ["bleibt.txt"]


def test_cleanup_owner_only(client, second_client):
    sid = _setup(client)
    me2 = second_client.get("/api/auth/me").json()
    client.post(f"/api/sources/{sid}/shares",
                json={"identifier": me2["email"], "permission": "annotate"})
    assert second_client.post(f"/api/sources/{sid}/missing/cleanup").status_code == 403
    assert second_client.get(f"/api/sources/{sid}/missing/summary").status_code == 403
