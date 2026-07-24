"""Tests für die FTS5-Suche über Name, Pfad und Notiztext."""

from __future__ import annotations

import uuid


def _setup_source_with_files(client):
    sid = client.post("/api/sources", json={"label": "Projekte", "kind": "local"}).json()["id"]
    entries = [
        {"path": "Angebote/kunde_mueller.pdf", "name": "kunde_mueller.pdf", "ext": "pdf"},
        {"path": "Rechnungen/2026_01.xlsx", "name": "2026_01.xlsx", "ext": "xlsx"},
        {"path": "notizen.md", "name": "notizen.md", "ext": "md"},
    ]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": entries, "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    return sid


def _search(client, q, **params):
    params["q"] = q
    r = client.get("/api/search", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_search_by_name_and_path(client):
    _setup_source_with_files(client)
    hits = _search(client, "mueller")
    assert any("kunde_mueller" in h["name"] for h in hits)

    # Präfix-Matching: "rech" findet den Ordner "Rechnungen".
    hits = _search(client, "rech")
    assert any("Rechnungen" in h["path"] for h in hits)


def test_search_by_annotation_text(client):
    sid = _setup_source_with_files(client)
    entry = [h for h in client.get(f"/api/sources/{sid}/entries").json()
             if h["name"] == "notizen.md"][0]
    client.post("/api/annotations", json={
        "entry_id": entry["entry_id"], "type": "note",
        "body": "Steuerunterlagen Vorbereitung"})

    hits = _search(client, "steuerunterlagen")
    assert len(hits) == 1
    assert hits[0]["name"] == "notizen.md"


def test_search_by_label(client):
    sid = _setup_source_with_files(client)
    entry = client.get(f"/api/sources/{sid}/entries").json()[0]
    client.post("/api/annotations", json={
        "entry_id": entry["entry_id"], "type": "label", "label_value": "dringend"})

    hits = _search(client, "dringend")
    assert any(h["entry_id"] == entry["entry_id"] for h in hits)


def test_search_empty_query_returns_nothing(client):
    _setup_source_with_files(client)
    assert _search(client, "") == []


def test_search_only_own_sources(client, second_client):
    # Nutzer A legt Quelle mit Datei an.
    _setup_source_with_files(client)
    # Nutzer B darf davon nichts finden.
    hits = _search(second_client, "mueller")
    assert hits == []
