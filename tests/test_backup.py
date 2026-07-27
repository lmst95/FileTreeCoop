"""Tests für das Voll-Backup der Datenbank."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid

from app.config import settings


def _as_admin(client, monkeypatch):
    """Macht den Nutzer des Clients zum Backup-Betreiber."""
    me = client.get("/api/auth/me").json()
    monkeypatch.setattr(settings, "backup_admins", {me["username"]})
    return me


def test_backup_download(client, monkeypatch):
    _as_admin(client, monkeypatch)
    sid = client.post(
        "/api/sources", json={"label": "Backup-Quelle", "kind": "local"}
    ).json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "a.txt", "name": "a.txt", "ext": "txt"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )

    r = client.get("/api/admin/backup.db")
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    assert ".sqlite3" in r.headers["content-disposition"]
    assert r.content.startswith(b"SQLite format 3\x00")

    # Die Kopie ist eine vollwertige, lesbare DB mit den aktuellen Daten.
    path = os.path.join(tempfile.gettempdir(), f"ftc_backup_{uuid.uuid4().hex}.db")
    try:
        with open(path, "wb") as fh:
            fh.write(r.content)
        conn = sqlite3.connect(path)
        labels = [row[0] for row in conn.execute("SELECT label FROM sources")]
        names = [row[0] for row in conn.execute("SELECT name FROM entries")]
        conn.close()
        assert "Backup-Quelle" in labels
        assert "a.txt" in names
    finally:
        os.remove(path)


def test_backup_requires_admin(client, second_client, monkeypatch):
    _as_admin(client, monkeypatch)
    assert second_client.get("/api/admin/backup.db").status_code == 403


def test_backup_default_admin_is_first_user(client, monkeypatch):
    """Ohne Konfiguration darf nur das zuerst registrierte Konto."""
    monkeypatch.setattr(settings, "backup_admins", set())
    me = client.get("/api/auth/me").json()
    # Die Fixture-Nutzer werden fortlaufend angelegt; der erste ist id == 1.
    expected = 200 if me["id"] == 1 else 403
    assert client.get("/api/admin/backup.db").status_code == expected


def test_backup_requires_login():
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/api/admin/backup.db").status_code == 401


def test_profile_page_shows_backup_card(client, monkeypatch):
    _as_admin(client, monkeypatch)
    assert "/api/admin/backup.db" in client.get("/profile").text


def test_profile_page_hides_backup_card(client, second_client, monkeypatch):
    _as_admin(client, monkeypatch)
    assert "/api/admin/backup.db" not in second_client.get("/profile").text
