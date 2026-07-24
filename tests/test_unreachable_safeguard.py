"""Tests für die Absicherung bei unvollständigen Scans.

Bei ``mark_missing=false`` (Client-Verhalten, wenn Einträge übersprungen
wurden, z. B. weil ein Ordner auf dem Netzlaufwerk kurz nicht erreichbar war)
darf der Finalize KEINE Einträge als „verschwunden“ markieren. Der komplette
Abbruch bei nicht erreichbarer Wurzel passiert clientseitig (der Scanner sendet
dann gar keine Abschluss-Batch) und ist hier daher nicht abgebildet.
"""

from __future__ import annotations

import uuid


def _new_source(client):
    r = client.post("/api/sources", json={"label": "Netz", "kind": "network"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _f(path):
    name = path.rsplit("/", 1)[-1]
    return {"path": path, "name": name, "is_dir": False,
            "size": 1, "mtime": 100.0, "ext": ""}


def _ingest(client, sid, entries, **extra):
    body = {"entries": entries, "finalize": True,
            "scan_id": uuid.uuid4().hex, **extra}
    r = client.post(f"/api/sources/{sid}/ingest", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_default_marks_missing(client):
    sid = _new_source(client)
    _ingest(client, sid, [_f("a.txt"), _f("b.txt")])          # Erst-Scan
    res = _ingest(client, sid, [_f("a.txt")])                  # b.txt fehlt
    assert res["marked_missing"] == 1
    assert res["missing_check_skipped"] is False
    summary = client.get(f"/api/sources/{sid}/missing/summary").json()
    assert summary["count"] == 1


def test_mark_missing_false_suppresses(client):
    sid = _new_source(client)
    _ingest(client, sid, [_f("a.txt"), _f("b.txt")])          # Erst-Scan
    # Unvollständiger Scan: b.txt fehlt, aber ein Ordner war unerreichbar.
    res = _ingest(client, sid, [_f("a.txt")],
                  mark_missing=False,
                  skipped=[{"path": "unerreichbar", "reason": "NotFoundError"}])
    assert res["marked_missing"] == 0
    assert res["missing_check_skipped"] is True
    # Nichts wurde als verschwunden markiert.
    summary = client.get(f"/api/sources/{sid}/missing/summary").json()
    assert summary["count"] == 0
    # Scan ist trotzdem abgeschlossen und der Skip persistiert.
    scans = client.get(f"/api/sources/{sid}/scans").json()
    assert scans[0]["status"] == "done"
    assert scans[0]["skipped"] == 1
