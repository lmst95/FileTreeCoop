"""Tests für die Exporte: iCal (.ics), Annotationen-CSV, JSON je Quelle."""

from __future__ import annotations

import uuid


def _setup(client):
    sid = client.post("/api/sources", json={"label": "Projekte", "kind": "local"}).json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "bericht.docx", "name": "bericht.docx", "ext": "docx"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    eid = client.get(f"/api/sources/{sid}/entries").json()[0]["entry_id"]
    client.post("/api/annotations", json={
        "entry_id": eid, "type": "todo", "body": "Kapitel 3 prüfen",
        "due_date": "2026-08-15"})
    client.post("/api/annotations", json={
        "entry_id": eid, "type": "label", "label_value": "wichtig"})
    return sid, eid


def test_ical_export(client):
    _setup(client)
    r = client.get("/api/export/calendar.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert "BEGIN:VCALENDAR" in body
    assert "BEGIN:VEVENT" in body
    assert "DTSTART;VALUE=DATE:20260815" in body
    assert "Kapitel 3 prüfen" in body
    # Erledigte/terminlose Einträge erzeugen keine weiteren Events.
    assert body.count("BEGIN:VEVENT") == 1


def test_csv_export(client):
    _setup(client)
    r = client.get("/api/export/annotations.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("quelle;pfad;datei;typ")
    assert len(lines) == 3  # Header + Todo + Label
    assert any("Kapitel 3 prüfen" in l for l in lines)
    assert any(";wichtig;" in l for l in lines)


def test_json_export(client):
    sid, _eid = _setup(client)
    r = client.get(f"/api/sources/{sid}/export.json")
    assert r.status_code == 200
    data = r.json()
    assert data["source"]["label"] == "Projekte"
    assert len(data["entries"]) == 1
    anns = data["entries"][0]["annotations"]
    assert {a["type"] for a in anns} == {"todo", "label"}
    assert anns[0]["author"] == "Testnutzer"


def test_export_respects_access(client, second_client):
    sid, _ = _setup(client)
    # Fremder ohne Freigabe: kein JSON-Export, leerer iCal.
    assert second_client.get(f"/api/sources/{sid}/export.json").status_code in (403, 404)
    body = second_client.get("/api/export/calendar.ics").text
    assert "BEGIN:VEVENT" not in body
