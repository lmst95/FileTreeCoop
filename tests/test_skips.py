"""Tests für übersprungene (nicht erreichbare) Einträge eines Scans.

Ein Scan eines Netzlaufwerks kann Einträge überspringen, wenn sie zwischen
Auflisten und Zugriff verschwinden oder kurz unerreichbar sind. Der Scanner
meldet sie in der Abschluss-Batch; sie werden persistiert und sind später
über die Scan-Historie abrufbar.
"""

from __future__ import annotations

import uuid


def _new_source(client, label="Netz"):
    r = client.post("/api/sources", json={"label": label, "kind": "network"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _f(path):
    name = path.rsplit("/", 1)[-1]
    return {"path": path, "name": name, "is_dir": False,
            "size": 1, "mtime": 100.0, "ext": ""}


def test_skips_persisted_and_counted(client):
    sid = _new_source(client)
    scan_id = uuid.uuid4().hex
    r = client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [_f("a.txt")], "finalize": True, "scan_id": scan_id,
              "skipped": [{"path": "gesperrt/ordner", "reason": "NotFoundError"},
                          {"path": "temp.tmp", "reason": "NotFoundError"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skipped"] == 2

    scans = client.get(f"/api/sources/{sid}/scans").json()
    assert scans[0]["skipped"] == 2

    skips = client.get(f"/api/sources/{sid}/scans/{scans[0]['id']}/skips").json()
    assert sorted(s["path"] for s in skips) == ["gesperrt/ordner", "temp.tmp"]
    assert all(s["reason"] == "NotFoundError" for s in skips)


def test_skips_deduped_across_batches(client):
    sid = _new_source(client)
    scan_id = uuid.uuid4().hex
    # Erste Batch (nicht finalize) mit einem Skip.
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [_f("a.txt")], "finalize": False, "scan_id": scan_id,
              "skipped": [{"path": "x", "reason": "NotFoundError"}]},
    )
    # Zweite Batch wiederholt denselben Skip + einen neuen -> nur "y" ist neu.
    r = client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [_f("b.txt")], "finalize": True, "scan_id": scan_id,
              "skipped": [{"path": "x", "reason": "NotFoundError"},
                          {"path": "y", "reason": "NotFoundError"}]},
    )
    assert r.json()["skipped"] == 1

    scans = client.get(f"/api/sources/{sid}/scans").json()
    assert scans[0]["skipped"] == 2
    skips = client.get(f"/api/sources/{sid}/scans/{scans[0]['id']}/skips").json()
    assert sorted(s["path"] for s in skips) == ["x", "y"]
