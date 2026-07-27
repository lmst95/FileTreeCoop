"""Tests für den Inhalts-Hash: Arbeitsliste, Übernahme, Umbenennungs-Erkennung."""

from __future__ import annotations

import hashlib
import uuid


def _new_source(client, label="Laptop"):
    r = client.post("/api/sources", json={"label": label, "kind": "local"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ingest(client, source_id, entries, scan_id=None):
    r = client.post(
        f"/api/sources/{source_id}/ingest",
        json={
            "entries": entries,
            "finalize": True,
            "scan_id": scan_id or uuid.uuid4().hex,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _file(path, name=None, size=10, mtime=1000.0, ext="txt"):
    return {
        "path": path,
        "name": name or path.split("/")[-1],
        "is_dir": False,
        "size": size,
        "mtime": mtime,
        "ext": ext,
    }


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _submit(client, sid, items):
    r = client.post(f"/api/sources/{sid}/hashes", json={"items": items})
    assert r.status_code == 200, r.text
    return r.json()


def test_hash_todo_lists_files_and_clears_after_submit(client):
    sid = _new_source(client)
    _ingest(client, sid, [_file("a.txt", size=5), _file("b.txt", size=7), {"path": "d", "name": "d", "is_dir": True}])

    todo = client.get(f"/api/sources/{sid}/hash-todo").json()
    # Ordner brauchen keinen Hash.
    assert sorted(t["path"] for t in todo) == ["a.txt", "b.txt"]
    # Kleine Dateien zuerst.
    assert todo[0]["path"] == "a.txt"

    _submit(client, sid, [
        {"path": "a.txt", "sha256": _digest("a"), "state": "ok", "size": 5, "mtime": 1000.0},
        {"path": "b.txt", "sha256": _digest("b"), "state": "ok", "size": 7, "mtime": 1000.0},
    ])
    assert client.get(f"/api/sources/{sid}/hash-todo").json() == []

    summary = client.get(f"/api/sources/{sid}/hash-summary").json()
    assert summary == {
        "files": 2, "hashed": 2, "pending": 0, "skipped": 0, "errors": 0,
        "duplicate_groups": 0,
    }


def test_submitted_file_leaves_the_todo_even_if_the_index_was_stale(client):
    """Die Arbeitsliste muss immer schrumpfen – sonst läuft der Nachlauf endlos.

    Der Scan kann eine Datei mit Größe 0 erfasst haben (nicht lesbar zu dem
    Zeitpunkt), während der Hash-Lauf sie einwandfrei liest und andere Werte
    meldet. Der Eintrag darf danach trotzdem nicht wieder auftauchen.
    """
    sid = _new_source(client)
    _ingest(client, sid, [_file("sperrig.dat", size=0, mtime=0.0)])

    _submit(client, sid, [
        {"path": "sperrig.dat", "sha256": _digest("inhalt"), "state": "ok",
         "size": 4711, "mtime": 1720000000.0},  # weicht vom Index ab
    ])

    assert client.get(f"/api/sources/{sid}/hash-todo").json() == []
    assert client.get(f"/api/sources/{sid}/hash-summary").json()["pending"] == 0


def test_changed_file_needs_a_fresh_hash(client):
    sid = _new_source(client)
    _ingest(client, sid, [_file("a.txt", size=5, mtime=1000.0)])
    _submit(client, sid, [
        {"path": "a.txt", "sha256": _digest("a"), "state": "ok", "size": 5, "mtime": 1000.0},
    ])
    assert client.get(f"/api/sources/{sid}/hash-todo").json() == []

    # Datei ändert sich -> der alte Hash gilt nicht mehr.
    _ingest(client, sid, [_file("a.txt", size=9, mtime=2000.0)])
    todo = client.get(f"/api/sources/{sid}/hash-todo").json()
    assert [t["path"] for t in todo] == ["a.txt"]
    assert client.get(f"/api/sources/{sid}/hash-summary").json()["pending"] == 1


def test_skipped_and_error_are_not_retried(client):
    sid = _new_source(client)
    _ingest(client, sid, [_file("big.iso", size=999), _file("locked.txt", size=3)])
    _submit(client, sid, [
        {"path": "big.iso", "state": "skipped", "size": 999, "mtime": 1000.0},
        {"path": "locked.txt", "state": "error", "size": 3, "mtime": 1000.0},
    ])

    assert client.get(f"/api/sources/{sid}/hash-todo").json() == []
    summary = client.get(f"/api/sources/{sid}/hash-summary").json()
    assert summary["skipped"] == 1
    assert summary["errors"] == 1
    assert summary["hashed"] == 0


def test_invalid_hash_is_rejected(client):
    sid = _new_source(client)
    _ingest(client, sid, [_file("a.txt")])
    r = client.post(
        f"/api/sources/{sid}/hashes",
        json={"items": [{"path": "a.txt", "sha256": "keinhash", "state": "ok",
                         "size": 10, "mtime": 1000.0}]},
    )
    assert r.status_code == 422


def test_rename_is_recognised_by_hash_and_notes_move_along(client):
    """Umbenennen ändert den Namen – nur der Inhalt verrät den Zusammenhang."""
    sid = _new_source(client)
    _ingest(client, sid, [_file("vertrag.pdf", size=100, mtime=5.0, ext="pdf")])
    entry = client.get(f"/api/sources/{sid}/entries").json()[0]
    client.post("/api/annotations", json={
        "entry_id": entry["entry_id"], "type": "note", "body": "unterschrieben"})
    _submit(client, sid, [
        {"path": "vertrag.pdf", "sha256": _digest("inhalt"), "state": "ok",
         "size": 100, "mtime": 5.0},
    ])

    # Umbenannt: neuer Name, gleicher Inhalt -> beim Scan „verschwunden + neu“.
    _ingest(client, sid, [_file("vertrag_final.pdf", size=100, mtime=5.0, ext="pdf")])
    listing = {h["path"]: h for h in client.get(f"/api/sources/{sid}/entries").json()}
    assert listing["vertrag.pdf"]["status"] == "missing"

    # Der Hash-Nachlauf erkennt die Datei wieder und nimmt die Notiz mit.
    res = _submit(client, sid, [
        {"path": "vertrag_final.pdf", "sha256": _digest("inhalt"), "state": "ok",
         "size": 100, "mtime": 5.0},
    ])
    assert res["reconciled"] == 1

    listing = {h["path"]: h for h in client.get(f"/api/sources/{sid}/entries").json()}
    assert "vertrag.pdf" not in listing
    assert [a["body"] for a in listing["vertrag_final.pdf"]["annotations"]] == [
        "unterschrieben"
    ]


def test_ambiguous_content_is_left_alone(client):
    """Zwei identische Dateien -> lieber nichts zuordnen als falsch zuordnen."""
    sid = _new_source(client)
    _ingest(client, sid, [_file("alt.txt", size=4, mtime=1.0)])
    _submit(client, sid, [
        {"path": "alt.txt", "sha256": _digest("gleich"), "state": "ok",
         "size": 4, "mtime": 1.0},
    ])
    # Zwei neue Dateien mit demselben Inhalt ersetzen die alte.
    _ingest(client, sid, [_file("kopie1.txt", size=4, mtime=9.0),
                          _file("kopie2.txt", size=4, mtime=9.0)])
    res = _submit(client, sid, [
        {"path": "kopie1.txt", "sha256": _digest("gleich"), "state": "ok",
         "size": 4, "mtime": 9.0},
        {"path": "kopie2.txt", "sha256": _digest("gleich"), "state": "ok",
         "size": 4, "mtime": 9.0},
    ])
    assert res["reconciled"] == 0
    paths = {h["path"] for h in client.get(f"/api/sources/{sid}/entries").json()}
    assert paths == {"alt.txt", "kopie1.txt", "kopie2.txt"}


def test_move_keeps_existing_hash(client):
    """Ein per Metadaten erkannter Umzug muss den Hash nicht neu berechnen."""
    sid = _new_source(client)
    _ingest(client, sid, [_file("doc.txt", size=12, mtime=7.0)])
    _submit(client, sid, [
        {"path": "doc.txt", "sha256": _digest("x"), "state": "ok",
         "size": 12, "mtime": 7.0},
    ])
    # Gleicher Name, Größe und mtime an neuem Ort -> Umzug-Erkennung greift.
    _ingest(client, sid, [{"path": "archiv", "name": "archiv", "is_dir": True},
                          _file("archiv/doc.txt", name="doc.txt", size=12, mtime=7.0)])
    assert client.get(f"/api/sources/{sid}/hash-todo").json() == []


def test_hashes_only_for_the_owner(client, second_client):
    sid = _new_source(client)
    _ingest(client, sid, [_file("a.txt")])
    client.post(f"/api/sources/{sid}/shares",
                json={"identifier": _other_username(second_client), "permission": "annotate"})

    assert second_client.get(f"/api/sources/{sid}/hash-todo").status_code == 403
    r = second_client.post(f"/api/sources/{sid}/hashes", json={"items": []})
    assert r.status_code == 403
    # Lesen darf, wer Zugriff hat.
    assert second_client.get(f"/api/sources/{sid}/hash-summary").status_code == 200


def _other_username(other_client) -> str:
    return other_client.get("/api/auth/me").json()["username"]
