"""Tests für die Speicherplatz-Ansicht: Kennzahlen, Drilldown, Duplikate."""

from __future__ import annotations

import hashlib
import time
import uuid


def _new_source(client, label="Laptop"):
    r = client.post("/api/sources", json={"label": label, "kind": "local"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ingest(client, sid, entries):
    r = client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": entries, "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text


def _file(path, size, mtime=None, ext="txt"):
    return {
        "path": path,
        "name": path.split("/")[-1],
        "is_dir": False,
        "size": size,
        "mtime": mtime if mtime is not None else time.time(),
        "ext": ext,
    }


def _dir(path):
    return {"path": path, "name": path.split("/")[-1], "is_dir": True}


def _tree(client, sid):
    """Ein kleiner Baum: projekte/ (60) und bilder/ (300), plus eine Datei oben."""
    _ingest(client, sid, [
        _dir("projekte"),
        _dir("projekte/2025"),
        _file("projekte/2025/plan.pdf", 50, ext="pdf"),
        _file("projekte/notiz.txt", 10),
        _dir("bilder"),
        _file("bilder/urlaub.jpg", 300, ext="jpg"),
        _file("liesmich.txt", 5),
    ])


def test_summary_counts_files_and_folders(client):
    sid = _new_source(client)
    _tree(client, sid)

    s = client.get("/api/storage/summary").json()
    assert s["total_size"] == 365
    assert s["files"] == 4
    assert s["dirs"] == 3
    assert s["sources"][0]["source_id"] == sid
    assert s["sources"][0]["size"] == 365


def test_folder_drilldown_aggregates_recursively(client):
    sid = _new_source(client)
    _tree(client, sid)

    root = client.get(f"/api/storage/folders?source_id={sid}").json()
    children = {c["name"]: c for c in root["children"]}
    assert root["total_size"] == 365
    # bilder ist größer als projekte -> steht vorn.
    assert root["children"][0]["name"] == "bilder"
    assert children["projekte"]["size"] == 60  # rekursiv über zwei Ebenen
    assert children["projekte"]["files"] == 2
    assert children["projekte"]["is_dir"] is True
    assert children["liesmich.txt"]["is_dir"] is False

    level = client.get(f"/api/storage/folders?source_id={sid}&parent=projekte").json()
    names = {c["name"]: c["size"] for c in level["children"]}
    assert names == {"2025": 50, "notiz.txt": 10}


def test_types_and_ages(client):
    sid = _new_source(client)
    old = time.time() - 3 * 365 * 86400
    _ingest(client, sid, [
        _file("a.pdf", 100, ext="pdf"),
        _file("b.pdf", 50, ext="pdf"),
        _file("alt.jpg", 20, mtime=old, ext="jpg"),
    ])

    types = client.get("/api/storage/types").json()
    assert types[0]["ext"] == "pdf"
    assert types[0]["size"] == 150
    assert types[0]["files"] == 2

    ages = client.get("/api/storage/ages").json()
    by_label = {a["label"]: a for a in ages}
    assert by_label["letzte 30 Tage"]["files"] == 2
    assert by_label["2–5 Jahre"]["files"] == 1
    # Jede Datei zählt in genau eine Klasse.
    assert sum(a["files"] for a in ages) == 3


def test_largest_and_oldest(client):
    sid = _new_source(client)
    old = time.time() - 3 * 365 * 86400
    _ingest(client, sid, [
        _file("klein.txt", 1),
        _file("gross.bin", 5000, ext="bin"),
        _file("alt.bin", 900, mtime=old, ext="bin"),
    ])

    largest = client.get("/api/storage/largest?limit=2").json()
    assert [e["name"] for e in largest] == ["gross.bin", "alt.bin"]
    assert largest[0]["source_label"] == "Laptop"

    oldest = client.get("/api/storage/oldest?days=730").json()
    assert [e["name"] for e in oldest] == ["alt.bin"]


def test_duplicates_need_hashes(client):
    sid = _new_source(client)
    _ingest(client, sid, [
        _file("a/report.pdf", 1000, mtime=1.0, ext="pdf"),
        _file("b/report.pdf", 1000, mtime=2.0, ext="pdf"),
        _file("c/anders.pdf", 1000, mtime=3.0, ext="pdf"),
    ])
    # Ohne Hashes weiß niemand, was doppelt liegt.
    assert client.get("/api/storage/duplicates").json() == []

    same = hashlib.sha256(b"same").hexdigest()
    other = hashlib.sha256(b"other").hexdigest()
    client.post(f"/api/sources/{sid}/hashes", json={"items": [
        {"path": "a/report.pdf", "sha256": same, "state": "ok", "size": 1000, "mtime": 1.0},
        {"path": "b/report.pdf", "sha256": same, "state": "ok", "size": 1000, "mtime": 2.0},
        {"path": "c/anders.pdf", "sha256": other, "state": "ok", "size": 1000, "mtime": 3.0},
    ]})

    groups = client.get("/api/storage/duplicates").json()
    assert len(groups) == 1
    assert groups[0]["count"] == 2
    assert groups[0]["wasted"] == 1000  # eine Kopie zu viel
    assert sorted(e["path"] for e in groups[0]["entries"]) == ["a/report.pdf", "b/report.pdf"]

    # Kleinkram lässt sich ausblenden.
    assert client.get("/api/storage/duplicates?min_size=2000").json() == []


def test_storage_respects_sharing(client, second_client):
    """Wer nur einen Teilbaum sieht, bekommt auch nur dessen Zahlen."""
    sid = _new_source(client)
    _tree(client, sid)
    other = second_client.get("/api/auth/me").json()["username"]
    r = client.post(
        f"/api/sources/{sid}/shares",
        json={"identifier": other, "permission": "read", "path_prefix": "projekte"},
    )
    assert r.status_code == 201, r.text

    mine = client.get("/api/storage/summary").json()
    theirs = second_client.get("/api/storage/summary").json()
    assert mine["total_size"] == 365
    assert theirs["total_size"] == 60  # nur der freigegebene Teilbaum
    assert theirs["files"] == 2

    largest = second_client.get("/api/storage/largest").json()
    assert {e["path"] for e in largest} == {"projekte/2025/plan.pdf", "projekte/notiz.txt"}


def test_unknown_source_is_404(client):
    assert client.get("/api/storage/summary?source_id=999999").status_code == 404


def test_storage_page_reachable(client):
    assert client.get("/storage").status_code == 200
