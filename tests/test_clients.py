"""Desktop-Client: Registrierung, Gerätetoken, Heartbeat, Befehle, Live-Deltas."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _make_source(client: TestClient, label: str = "Laptop") -> int:
    r = client.post("/api/sources", json={"label": label, "kind": "local"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _credentials(client: TestClient) -> tuple[str, str]:
    """Anmeldedaten des eingeloggten Test-Nutzers (Passwort ist fix, s. conftest)."""
    me = client.get("/api/auth/me").json()
    return me["username"], "geheim123"


def _register_client(client: TestClient, name: str = "Testrechner") -> str:
    ident, password = _credentials(client)
    r = client.post(
        "/api/clients/register",
        json={
            "identifier": ident,
            "password": password,
            "name": name,
            "hostname": "test-host",
            "platform": "win32",
            "version": "1.0.0",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _agent(token: str) -> TestClient:
    """Ein TestClient, der sich wie der Desktop-Client per Bearer ausweist.

    Bewusst ohne Cookies: so ist sichergestellt, dass wirklich der Token trägt
    und nicht versehentlich eine Session mitläuft.
    """
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


# --- Registrierung -----------------------------------------------------------

def test_register_returns_token_once(client):
    r = client.post(
        "/api/clients/register",
        json={
            "identifier": _credentials(client)[0],
            "password": "geheim123",
            "name": "Bürorechner",
            "hostname": "buero-pc",
            "platform": "win32",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["token"]
    assert data["name"] == "Bürorechner"

    # Der Token taucht in der Verwaltungsansicht nirgends wieder auf.
    listing = client.get("/api/clients").json()
    assert len(listing) == 1
    assert "token" not in listing[0]


def test_register_rejects_wrong_password(client):
    r = client.post(
        "/api/clients/register",
        json={"identifier": _credentials(client)[0], "password": "falsch", "name": "X"},
    )
    assert r.status_code == 401


def test_reregister_same_machine_rotates_token(client):
    """Neuinstallation legt keine Karteileiche an, sondern erneuert den Token."""
    first = _register_client(client, "Testrechner")
    second = _register_client(client, "Testrechner")
    assert first != second
    assert len(client.get("/api/clients").json()) == 1
    # Der alte Token ist damit sofort wertlos.
    assert _agent(first).post("/api/clients/heartbeat", json={}).status_code == 401
    assert _agent(second).post("/api/clients/heartbeat", json={}).status_code == 200


# --- Gerätetoken als Authentifizierung ---------------------------------------

def test_token_authenticates_against_ingest(client):
    """Der Client nutzt dieselben Endpunkte wie der Browser-Scanner."""
    source_id = _make_source(client)
    agent = _agent(_register_client(client))

    r = agent.post(
        f"/api/sources/{source_id}/ingest",
        json={
            "entries": [
                {"path": "a.txt", "name": "a.txt", "is_dir": False, "size": 5, "mtime": 1.0},
            ],
            "scan_id": str(uuid.uuid4()),
            "finalize": True,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["upserted"] == 1
    # Und die Daten liegen beim richtigen Nutzer.
    hits = client.get("/api/search?q=a.txt").json()
    assert [h["name"] for h in hits] == ["a.txt"]


def test_token_cannot_download_backup_or_change_password(client):
    """Ein gestohlener Gerätetoken darf das Konto nicht übernehmen."""
    agent = _agent(_register_client(client))
    assert agent.get("/api/admin/backup.db").status_code == 401
    assert agent.post(
        "/api/auth/me/password",
        json={"current_password": "geheim123", "new_password": "neuneu123"},
    ).status_code == 401
    # Auch die Geräteverwaltung selbst bleibt dem Browser vorbehalten.
    assert agent.get("/api/clients").status_code == 401


def test_invalid_token_is_rejected(client):
    assert _agent("kein-echter-token").post(
        "/api/clients/heartbeat", json={}
    ).status_code == 401


# --- Heartbeat + Ordner-Konfiguration ----------------------------------------

def test_heartbeat_reports_folders_and_online_state(client):
    source_id = _make_source(client, "Projekte")
    agent = _agent(_register_client(client))

    r = agent.post(
        "/api/clients/heartbeat",
        json={
            "status_text": "1 Ordner aktuell",
            "folders": [
                {
                    "source_id": source_id,
                    "local_path": "C:\\Projekte",
                    "enabled": True,
                    "hash_enabled": True,
                    "scan_interval_minutes": 30,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["commands"] == []

    (info,) = client.get("/api/clients").json()
    assert info["online"] is True
    assert info["status_text"] == "1 Ordner aktuell"
    (folder,) = info["folders"]
    assert folder["local_path"] == "C:\\Projekte"
    assert folder["hash_enabled"] is True
    assert folder["source_label"] == "Projekte"


def test_heartbeat_ignores_foreign_sources(client, second_client):
    """Ein Client darf nur Ordner zu Quellen seines Besitzers melden."""
    foreign_id = _make_source(second_client, "Fremde Quelle")
    agent = _agent(_register_client(client))

    agent.post(
        "/api/clients/heartbeat",
        json={"folders": [{"source_id": foreign_id, "local_path": "C:\\Fremd"}]},
    )
    (info,) = client.get("/api/clients").json()
    assert info["folders"] == []


def test_heartbeat_removes_folders_that_are_gone(client):
    source_id = _make_source(client)
    agent = _agent(_register_client(client))
    agent.post(
        "/api/clients/heartbeat",
        json={"folders": [{"source_id": source_id, "local_path": "C:\\A"}]},
    )
    assert len(client.get("/api/clients").json()[0]["folders"]) == 1

    agent.post("/api/clients/heartbeat", json={"folders": []})
    assert client.get("/api/clients").json()[0]["folders"] == []


# --- „Ordner öffnen“ ---------------------------------------------------------

def _connected_agent(client, source_id: int, path: str = "C:\\Projekte"):
    agent = _agent(_register_client(client))
    agent.post(
        "/api/clients/heartbeat",
        json={"folders": [{"source_id": source_id, "local_path": path}]},
    )
    return agent


def test_open_folder_queues_command_and_client_picks_it_up(client):
    source_id = _make_source(client)
    agent = _connected_agent(client, source_id)

    r = client.post("/api/clients/open", json={"source_id": source_id, "path": ""})
    assert r.status_code == 200, r.text
    command_id = r.json()["command_id"]

    # Der Client holt den Befehl beim nächsten Heartbeat ab.
    commands = agent.post("/api/clients/heartbeat", json={}).json()["commands"]
    assert len(commands) == 1
    assert commands[0]["command"] == "open_folder"
    assert commands[0]["payload"] == {"source_id": source_id, "path": "", "is_dir": True}

    # Und nur einmal – ein zweiter Heartbeat liefert ihn nicht erneut.
    assert agent.post("/api/clients/heartbeat", json={}).json()["commands"] == []

    agent.post(f"/api/clients/commands/{command_id}/ack", json={"status": "done"})
    assert client.get(f"/api/clients/commands/{command_id}").json()["status"] == "done"


def test_open_folder_without_client_gives_helpful_error(client):
    source_id = _make_source(client)
    r = client.post("/api/clients/open", json={"source_id": source_id})
    assert r.status_code == 404
    assert "Desktop-Client" in r.json()["detail"]


def test_open_folder_checks_the_path_exists(client):
    source_id = _make_source(client)
    _connected_agent(client, source_id)
    r = client.post("/api/clients/open", json={"source_id": source_id, "path": "gibtsnicht"})
    assert r.status_code == 404


def test_reachable_sources_lists_only_covered_sources(client):
    covered = _make_source(client, "Mit Client")
    bare = _make_source(client, "Ohne Client")
    _connected_agent(client, covered)

    reachable = client.get("/api/clients/reachable-sources").json()
    assert covered in reachable
    assert bare not in reachable


def test_deleting_client_revokes_its_token(client):
    source_id = _make_source(client)
    agent = _connected_agent(client, source_id)
    client_id = client.get("/api/clients").json()[0]["id"]

    assert client.delete(f"/api/clients/{client_id}").status_code == 204
    assert agent.post("/api/clients/heartbeat", json={}).status_code == 401
    # Der Index bleibt bestehen, nur die Betreuung endet.
    assert client.get("/api/clients/reachable-sources").json() == []


def test_remote_pause_reaches_the_client(client):
    source_id = _make_source(client)
    agent = _connected_agent(client, source_id)
    client_id = client.get("/api/clients").json()[0]["id"]

    client.patch(f"/api/clients/{client_id}", json={"paused": True})
    assert agent.post("/api/clients/heartbeat", json={}).json()["paused"] is True


# --- Live-Deltas -------------------------------------------------------------

def _ingest(agent, source_id, entries, **kwargs):
    body = {"entries": entries, "scan_id": str(uuid.uuid4()), "finalize": True}
    body.update(kwargs)
    r = agent.post(f"/api/sources/{source_id}/ingest", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _entry(path: str, size: int = 10, mtime: float = 100.0) -> dict:
    return {
        "path": path, "name": path.rsplit("/", 1)[-1],
        "is_dir": False, "size": size, "mtime": mtime,
    }


def test_live_delta_updates_only_named_paths(client):
    """Ein Live-Delta darf nichts anfassen, was es nicht erwähnt."""
    source_id = _make_source(client)
    agent = _agent(_register_client(client))
    _ingest(agent, source_id, [_entry("a.txt"), _entry("b.txt"), _entry("c.txt")])

    # Nur b.txt ändert sich – a und c bleiben unangetastet „vorhanden“.
    _ingest(agent, source_id, [_entry("b.txt", size=99)], kind="live", mark_missing=False)

    hits = {h["name"]: h for h in client.get("/api/search?q=txt").json()}
    assert len(hits) == 3
    assert all(h["status"] == "present" for h in hits.values())


def test_live_delta_reports_deletions_via_removed(client):
    source_id = _make_source(client)
    agent = _agent(_register_client(client))
    _ingest(agent, source_id, [_entry("a.txt"), _entry("b.txt")])

    result = _ingest(
        agent, source_id, [], kind="live", mark_missing=False, removed=["a.txt"]
    )
    assert result["removed"] == 1

    hits = {h["name"]: h["status"] for h in client.get("/api/search?q=txt").json()}
    assert hits == {"a.txt": "missing", "b.txt": "present"}


def test_live_delta_keeps_annotations_on_deleted_files(client):
    """Löschen markiert nur – Notizen überleben, wie überall in der App."""
    source_id = _make_source(client)
    agent = _agent(_register_client(client))
    _ingest(agent, source_id, [_entry("wichtig.txt")])
    entry_id = client.get("/api/search?q=wichtig").json()[0]["entry_id"]
    client.post(
        "/api/annotations",
        json={"entry_id": entry_id, "type": "note", "body": "Nicht verlieren"},
    )

    _ingest(agent, source_id, [], kind="live", mark_missing=False, removed=["wichtig.txt"])
    anns = client.get(f"/api/annotations/by-entry/{entry_id}").json()
    assert [a["body"] for a in anns] == ["Nicht verlieren"]


def test_live_scans_stay_out_of_dashboard_and_activity(client):
    """Live-Deltas fallen im Minutentakt an – sie dürfen die Anzeigen nicht fluten."""
    source_id = _make_source(client)
    agent = _agent(_register_client(client))
    _ingest(agent, source_id, [_entry("a.txt")])  # Voll-Scan (Erst-Import)
    _ingest(agent, source_id, [_entry("b.txt")], kind="live", mark_missing=False)

    (source,) = [s for s in client.get("/api/sources").json() if s["id"] == source_id]
    assert source["last_scan"]["kind"] == "full"
    assert source["last_scan"]["initial"] is True

    scans = client.get(f"/api/sources/{source_id}/scans").json()
    assert [s["kind"] for s in scans] == ["full"]
    with_live = client.get(f"/api/sources/{source_id}/scans?include_live=true").json()
    assert sorted(s["kind"] for s in with_live) == ["full", "live"]

    feed = client.get("/api/activity").json()["items"]
    assert [i for i in feed if i["kind"] == "scan"] != []
    assert all(i.get("initial") is not None for i in feed if i["kind"] == "scan")
    assert len([i for i in feed if i["kind"] == "scan"]) == 1


def test_live_delta_before_first_full_scan_still_records_changes(client):
    """Ein Live-Delta ist nie der „Erst-Import“ – sonst gingen seine Änderungen unter."""
    source_id = _make_source(client)
    agent = _agent(_register_client(client))

    _ingest(agent, source_id, [_entry("neu.txt")], kind="live", mark_missing=False)
    scans = client.get(f"/api/sources/{source_id}/scans?include_live=true").json()
    assert scans[0]["initial"] is False
    assert scans[0]["added"] == 1

    changes = client.get(
        f"/api/sources/{source_id}/scans/{scans[0]['id']}/changes"
    ).json()
    assert [c["change"] for c in changes] == ["added"]


def test_clients_page_reachable(client):
    assert client.get("/clients").status_code == 200
