"""Tests für die strukturellen Suchfilter und den Suchassistenten.

Das Modell wird nicht wirklich befragt: ``service.run_completion`` wird ersetzt,
sodass die Tests genau das prüfen, was hier zu verantworten ist – Prompt-Bau,
Validierung der Antwort und die daraus gebaute Suche.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app import search_assist
from app.llm import service
from app.llm.providers import LLMError
from app.routers import search as search_router


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


def _file(path, size=10, mtime=None, ext="txt"):
    return {
        "path": path,
        "name": path.split("/")[-1],
        "is_dir": False,
        "size": size,
        "mtime": mtime if mtime is not None else time.time(),
        "ext": ext,
    }


def _llm_setting(client, label="Testmodell"):
    """Verbindung + Setting anlegen, dem Feature „Suche“ zugeordnet."""
    conn = client.post("/api/llm/connections", json={
        "label": "lokal", "provider_type": "ollama",
        "base_url": "http://localhost:11434", "default_model": "llama3",
    })
    assert conn.status_code == 201, conn.text
    setting = client.post("/api/llm/settings", json={
        "label": label, "connection_id": conn.json()["id"],
        "model": "llama3", "features": ["search"],
    })
    assert setting.status_code == 201, setting.text
    return setting.json()["id"]


# --- Filter ohne Assistent ---------------------------------------------------

def test_filters_work_without_search_text(client):
    sid = _new_source(client)
    old = time.time() - 400 * 86400
    _ingest(client, sid, [
        _file("gross.pdf", size=5_000_000, mtime=old, ext="pdf"),
        _file("klein.pdf", size=100, ext="pdf"),
        _file("notiz.txt", size=5_000_000, ext="txt"),
    ])

    hits = client.get("/api/search?ext=pdf&min_size=1000").json()
    assert [h["name"] for h in hits] == ["gross.pdf"]

    # Ohne Text und ohne Filter bleibt es leer – kein „alles ausgeben“.
    assert client.get("/api/search").json() == []


def test_date_filters(client):
    sid = _new_source(client)
    _ingest(client, sid, [
        _file("neu.txt", mtime=time.time()),
        _file("alt.txt", mtime=time.time() - 900 * 86400),
    ])
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=30)).isoformat()
    hits = client.get(f"/api/search?modified_after={cutoff}").json()
    assert [h["name"] for h in hits] == ["neu.txt"]

    hits = client.get(f"/api/search?modified_before={cutoff}").json()
    assert [h["name"] for h in hits] == ["alt.txt"]


def test_search_scope_can_be_narrowed(client):
    """Ein Ordner „Angebote“ soll nicht jede Datei darunter zum Treffer machen."""
    sid = _new_source(client)
    _ingest(client, sid, [
        {"path": "angebote", "name": "angebote", "is_dir": True},
        _file("angebote/rechnung.pdf", ext="pdf"),
        _file("kunden/angebot_mueller.pdf", ext="pdf"),
    ])

    # Überall (Standard): Ordner, Datei darunter und die passend benannte Datei.
    everywhere = {h["name"] for h in client.get("/api/search?q=angebot").json()}
    assert everywhere == {"angebote", "rechnung.pdf", "angebot_mueller.pdf"}

    # Nur der Dateiname zählt.
    only_name = {h["name"] for h in client.get("/api/search?q=angebot&fields=name").json()}
    assert only_name == {"angebote", "angebot_mueller.pdf"}

    # Nur der Pfad – der Ordner selbst hat „angebote“ nicht im Pfad-Präfix …
    only_path = {h["path"] for h in client.get("/api/search?q=angebot&fields=path").json()}
    assert only_path == {"angebote", "angebote/rechnung.pdf", "kunden/angebot_mueller.pdf"}

    # Unbekannter Bereich verhält sich wie „überall“.
    assert {h["name"] for h in client.get("/api/search?q=angebot&fields=quatsch").json()} == everywhere


def test_search_can_be_limited_to_files_or_folders(client):
    sid = _new_source(client)
    _ingest(client, sid, [
        {"path": "berichte", "name": "berichte", "is_dir": True},
        _file("berichte/bericht.pdf", ext="pdf"),
    ])

    files = client.get("/api/search?q=bericht&is_dir=false").json()
    assert [h["name"] for h in files] == ["bericht.pdf"]

    dirs = client.get("/api/search?q=bericht&is_dir=true").json()
    assert [h["name"] for h in dirs] == ["berichte"]

    # Auch ohne Suchtext: „zeig mir nur Ordner“ ist ein gültiger Filter.
    assert [h["name"] for h in client.get("/api/search?is_dir=true").json()] == ["berichte"]


def test_search_in_notes_only(client):
    sid = _new_source(client)
    _ingest(client, sid, [_file("x.txt"), _file("kündigung.txt")])
    entry = next(
        h for h in client.get(f"/api/sources/{sid}/entries").json() if h["name"] == "x.txt"
    )
    client.post("/api/annotations", json={
        "entry_id": entry["entry_id"], "type": "note", "body": "Kündigung liegt bei"})

    hits = client.get("/api/search?q=kündigung&fields=notes").json()
    assert [h["name"] for h in hits] == ["x.txt"]


def test_filters_combine_with_text(client):
    sid = _new_source(client)
    _ingest(client, sid, [
        _file("angebot_mueller.pdf", size=900, ext="pdf"),
        _file("angebot_mueller.docx", size=900, ext="docx"),
    ])
    hits = client.get("/api/search?q=angebot&ext=docx").json()
    assert [h["name"] for h in hits] == ["angebot_mueller.docx"]


# --- Antwort des Modells auswerten -------------------------------------------

def test_extract_json_survives_code_fences():
    raw = 'Klar!\n```json\n{"query": "vertrag", "ext": ["pdf"]}\n```\n'
    assert search_assist.extract_json(raw) == {"query": "vertrag", "ext": ["pdf"]}


def test_extract_json_rejects_prose():
    with pytest.raises(ValueError):
        search_assist.extract_json("Dazu kann ich nichts sagen.")


def test_coerce_drops_nonsense():
    parsed = search_assist.coerce(
        {
            "query": "  vertrag  ",
            "source_id": 42,  # nicht zugänglich -> raus
            "status": "irgendwas",  # unbekannt -> raus
            "ext": [".PDF", "docx", "!!", 7],
            "modified_after": "kein datum",
            "min_size": -5,  # negativ -> raus
            "is_dir": "ja",  # kein bool -> raus
        },
        allowed_source_ids={1},
    )
    assert parsed.query == "vertrag"
    assert parsed.filters.source_id is None
    assert parsed.filters.status is None
    assert parsed.filters.ext == ["pdf", "docx"]
    assert parsed.filters.modified_after is None
    assert parsed.filters.min_size is None
    assert parsed.filters.is_dir is None


def test_coerce_swaps_reversed_range():
    parsed = search_assist.coerce(
        {"modified_after": "2026-05-01", "modified_before": "2026-01-01"},
        allowed_source_ids=set(),
    )
    assert parsed.modified_after.isoformat() == "2026-01-01"
    assert parsed.modified_before.isoformat() == "2026-05-01"
    assert parsed.filters.modified_after < parsed.filters.modified_before


# --- Der Endpunkt ------------------------------------------------------------

def test_assist_translates_question_into_a_search(client, monkeypatch):
    sid = _new_source(client)
    _ingest(client, sid, [
        _file("angebot_2025.pdf", size=2_000_000, ext="pdf"),
        _file("angebot_2025.txt", size=2_000_000, ext="txt"),
    ])
    setting_id = _llm_setting(client)

    seen = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return (
            '{"query": "angebot", "ext": ["pdf"], "min_size": 1048576, '
            '"explanation": "Große PDFs zum Angebot."}'
        )

    monkeypatch.setattr(service, "run_completion", fake_completion)

    r = client.post("/api/search/assist", json={
        "question": "große Angebots-PDFs", "setting_id": setting_id})
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["filters"]["query"] == "angebot"
    assert data["filters"]["ext"] == ["pdf"]
    assert data["explanation"] == "Große PDFs zum Angebot."
    assert [h["name"] for h in data["hits"]] == ["angebot_2025.pdf"]

    # Dem Modell werden Frage und Quellen-Kontext geschickt – sonst nichts.
    assert seen["input_text"] == "große Angebots-PDFs"
    assert "Laptop" in seen["prompt_body"]
    assert "{{input}}" in seen["prompt_body"]


def test_assist_reports_unusable_answers(client, monkeypatch):
    _new_source(client)
    setting_id = _llm_setting(client)
    monkeypatch.setattr(service, "run_completion", lambda **_k: "Weiß ich nicht.")

    r = client.post("/api/search/assist", json={
        "question": "wo liegt das?", "setting_id": setting_id})
    assert r.status_code == 502
    assert "nicht verwertbar" in r.json()["detail"]


def test_assist_passes_llm_errors_through(client, monkeypatch):
    _new_source(client)
    setting_id = _llm_setting(client)

    def boom(**_kwargs):
        raise LLMError("Modell nicht erreichbar")

    monkeypatch.setattr(service, "run_completion", boom)
    r = client.post("/api/search/assist", json={
        "question": "egal", "setting_id": setting_id})
    assert r.status_code == 502
    assert "nicht erreichbar" in r.json()["detail"]


def test_assist_rejects_foreign_setting(client, second_client):
    setting_id = _llm_setting(second_client, label="fremdes Modell")
    r = client.post("/api/search/assist", json={
        "question": "egal", "setting_id": setting_id})
    assert r.status_code == 404


def test_search_feature_is_offered(client):
    """Das Feature „Suche“ taucht in der generischen LLM-Zuordnung auf."""
    meta = client.get("/api/llm/meta").json()
    assert "search" in {f["key"] for f in meta["features"]}

    setting_id = _llm_setting(client)
    opts = client.get("/api/llm/features/search").json()
    assert [s["id"] for s in opts["settings"]] == [setting_id]
