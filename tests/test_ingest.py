"""Tests für Quellen-Anlage und den Ingest-Upsert samt Missing-Logik."""

from __future__ import annotations

import uuid


def _new_source(client, label="Laptop"):
    r = client.post("/api/sources", json={"label": label, "kind": "local"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ingest(client, source_id, entries, finalize, scan_id):
    r = client.post(
        f"/api/sources/{source_id}/ingest",
        json={"entries": entries, "finalize": finalize, "scan_id": scan_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_ingest_is_idempotent(client):
    sid = _new_source(client)
    entries = [
        {"path": "a.txt", "name": "a.txt", "is_dir": False, "size": 1, "mtime": 1.0, "ext": "txt"},
        {"path": "sub", "name": "sub", "is_dir": True},
        {"path": "sub/b.pdf", "name": "b.pdf", "size": 2, "ext": "pdf"},
    ]
    scan = uuid.uuid4().hex
    res = _ingest(client, sid, entries, finalize=True, scan_id=scan)
    assert res["upserted"] == 3

    # Zweiter, identischer Scan darf keine Duplikate erzeugen.
    res2 = _ingest(client, sid, entries, finalize=True, scan_id=uuid.uuid4().hex)
    assert res2["upserted"] == 3

    listing = client.get(f"/api/sources/{sid}/entries").json()
    assert len(listing) == 3
    paths = sorted(h["path"] for h in listing)
    assert paths == ["a.txt", "sub", "sub/b.pdf"]


def test_rescan_marks_missing(client):
    sid = _new_source(client)
    first = [
        {"path": "keep.txt", "name": "keep.txt"},
        {"path": "gone.txt", "name": "gone.txt"},
    ]
    _ingest(client, sid, first, finalize=True, scan_id=uuid.uuid4().hex)

    # Zweiter Scan enthält "gone.txt" nicht mehr -> muss als missing markiert werden.
    res = _ingest(
        client, sid, [{"path": "keep.txt", "name": "keep.txt"}],
        finalize=True, scan_id=uuid.uuid4().hex,
    )
    assert res["marked_missing"] == 1

    listing = {h["path"]: h for h in client.get(f"/api/sources/{sid}/entries").json()}
    assert listing["keep.txt"]["status"] == "present"
    assert listing["gone.txt"]["status"] == "missing"


def test_annotation_survives_missing(client):
    sid = _new_source(client)
    _ingest(client, sid, [{"path": "doc.txt", "name": "doc.txt"}],
            finalize=True, scan_id=uuid.uuid4().hex)
    entry = client.get(f"/api/sources/{sid}/entries").json()[0]

    r = client.post("/api/annotations", json={
        "entry_id": entry["entry_id"], "type": "note", "body": "wichtig"})
    assert r.status_code == 201

    # Datei verschwindet beim nächsten Scan.
    _ingest(client, sid, [], finalize=True, scan_id=uuid.uuid4().hex)

    again = client.get(f"/api/sources/{sid}/entries").json()[0]
    assert again["status"] == "missing"
    assert len(again["annotations"]) == 1
    assert again["annotations"][0]["body"] == "wichtig"
