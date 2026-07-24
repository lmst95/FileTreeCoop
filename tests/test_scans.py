"""Tests für Scan-Läufe: Diff-Zähler, Change-Historie und Umzug-Erkennung."""

from __future__ import annotations

import uuid


def _new_source(client, label="Laptop"):
    r = client.post("/api/sources", json={"label": label, "kind": "local"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ingest(client, source_id, entries, finalize=True, scan_id=None):
    r = client.post(
        f"/api/sources/{source_id}/ingest",
        json={
            "entries": entries,
            "finalize": finalize,
            "scan_id": scan_id or uuid.uuid4().hex,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _f(path, size=1, mtime=100.0):
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return {"path": path, "name": name, "is_dir": False,
            "size": size, "mtime": mtime, "ext": ext}


def _scans(client, sid):
    r = client.get(f"/api/sources/{sid}/scans")
    assert r.status_code == 200, r.text
    return r.json()


def _changes(client, sid, scan_id):
    r = client.get(f"/api/sources/{sid}/scans/{scan_id}/changes")
    assert r.status_code == 200, r.text
    return r.json()


def test_initial_scan_counts_but_writes_no_changes(client):
    sid = _new_source(client)
    res = _ingest(client, sid, [_f("a.txt"), _f("b.txt")])
    assert res["added"] == 2

    scans = _scans(client, sid)
    assert len(scans) == 1
    assert scans[0]["initial"] is True
    assert scans[0]["added"] == 2
    assert scans[0]["status"] == "done"
    # Erst-Scan: keine per-Eintrag-Änderungszeilen.
    assert _changes(client, sid, scans[0]["id"]) == []


def test_rescan_classifies_changes(client):
    sid = _new_source(client)
    _ingest(client, sid, [_f("same.txt"), _f("mod.txt", size=5), _f("gone.txt")])

    res = _ingest(client, sid, [
        _f("same.txt"),                  # unverändert
        _f("mod.txt", size=9),           # geändert
        _f("neu.txt"),                   # neu
    ])
    assert res["added"] == 1
    assert res["changed"] == 1
    assert res["marked_missing"] == 1

    scan = _scans(client, sid)[0]
    assert scan["initial"] is False
    assert (scan["added"], scan["changed"], scan["unchanged"], scan["missing"]) == (1, 1, 1, 1)

    changes = {(c["change"], c["path"]) for c in _changes(client, sid, scan["id"])}
    assert ("added", "neu.txt") in changes
    assert ("modified", "mod.txt") in changes
    assert ("missing", "gone.txt") in changes


def test_reappeared_is_tracked(client):
    sid = _new_source(client)
    _ingest(client, sid, [_f("blink.txt")])
    _ingest(client, sid, [])            # -> missing
    res = _ingest(client, sid, [_f("blink.txt")])
    assert res["reappeared"] == 1

    scan = _scans(client, sid)[0]
    assert scan["reappeared"] == 1
    changes = _changes(client, sid, scan["id"])
    assert [c["change"] for c in changes] == ["reappeared"]


def test_move_detection_transfers_annotations(client):
    sid = _new_source(client)
    _ingest(client, sid, [_f("alt/datei.pdf", size=42, mtime=777.0)])
    entry = client.get(f"/api/sources/{sid}/entries").json()[0]
    r = client.post("/api/annotations", json={
        "entry_id": entry["entry_id"], "type": "note", "body": "wandert mit"})
    assert r.status_code == 201

    # Gleiche Datei (Name+Größe+mtime) an neuem Ort -> Umzug, kein Verlust.
    res = _ingest(client, sid, [_f("neu/datei.pdf", size=42, mtime=777.0)])
    assert res["moved"] == 1
    assert res["marked_missing"] == 0

    listing = client.get(f"/api/sources/{sid}/entries").json()
    assert len(listing) == 1
    hit = listing[0]
    assert hit["path"] == "neu/datei.pdf"
    assert hit["status"] == "present"
    assert [a["body"] for a in hit["annotations"]] == ["wandert mit"]

    scan = _scans(client, sid)[0]
    assert scan["moved"] == 1 and scan["added"] == 0 and scan["missing"] == 0
    changes = _changes(client, sid, scan["id"])
    assert changes[0]["change"] == "moved"
    assert changes[0]["old_path"] == "alt/datei.pdf"
    assert changes[0]["path"] == "neu/datei.pdf"


def test_ambiguous_moves_stay_missing(client):
    sid = _new_source(client)
    # Zwei identische Kandidaten -> mehrdeutig, keine Umzug-Zuordnung.
    _ingest(client, sid, [
        _f("a/gleich.txt", size=7, mtime=5.0),
        _f("b/gleich.txt", size=7, mtime=5.0),
    ])
    res = _ingest(client, sid, [_f("c/gleich.txt", size=7, mtime=5.0)])
    assert res["moved"] == 0
    assert res["added"] == 1
    assert res["marked_missing"] == 2


def test_scan_source_mismatch_is_rejected(client):
    sid_a = _new_source(client, "A")
    sid_b = _new_source(client, "B")
    scan_id = uuid.uuid4().hex
    _ingest(client, sid_a, [_f("x.txt")], finalize=False, scan_id=scan_id)
    r = client.post(
        f"/api/sources/{sid_b}/ingest",
        json={"entries": [], "finalize": True, "scan_id": scan_id},
    )
    assert r.status_code == 409
