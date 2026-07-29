"""Tests für Ignorierregeln: gespeicherte Ausschlüsse aus der Suche."""

from __future__ import annotations

import uuid


def _setup(client):
    sid = client.post(
        "/api/sources", json={"label": "Projekte", "kind": "local"}
    ).json()["id"]
    entries = [
        {"path": "Archiv/2019", "name": "2019", "is_dir": True},
        {"path": "Archiv/2019/angebot_alt.pdf", "name": "angebot_alt.pdf", "ext": "pdf"},
        {"path": "Aktuell/angebot_neu.pdf", "name": "angebot_neu.pdf", "ext": "pdf"},
        {"path": "Aktuell/angebot.tmp", "name": "angebot.tmp", "ext": "tmp"},
        {"path": "Code/node_modules/angebot.js", "name": "angebot.js", "ext": "js"},
    ]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": entries, "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    return sid


def _names(client, q="angebot", **params):
    params["q"] = q
    r = client.get("/api/search", params=params)
    assert r.status_code == 200, r.text
    return {h["name"] for h in r.json()}


def _add(client, **rule):
    r = client.post("/api/ignores", json=rule)
    assert r.status_code == 201, r.text
    return r.json()


def test_path_rule_hides_folder_and_everything_below(client):
    _setup(client)
    assert "angebot_alt.pdf" in _names(client)

    _add(client, kind="path", pattern="Archiv/2019")

    names = _names(client)
    assert "angebot_alt.pdf" not in names  # Datei im Unterbaum
    assert "angebot_neu.pdf" in names  # Rest unberührt
    # Der Ordner selbst verschwindet ebenfalls aus der Suche.
    assert "2019" not in _names(client, "2019")


def test_name_rule_hides_matching_files_everywhere(client):
    _setup(client)
    _add(client, kind="name", pattern="*.tmp")
    assert "angebot.tmp" not in _names(client)
    assert "angebot_neu.pdf" in _names(client)


def test_path_rule_with_wildcards(client):
    _setup(client)
    _add(client, kind="path", pattern="**/node_modules")
    assert "angebot.js" not in _names(client)
    assert "angebot_neu.pdf" in _names(client)


def test_rules_apply_to_all_modes(client):
    _setup(client)
    _add(client, kind="name", pattern="*.tmp")
    assert _names(client, "*.tmp", mode="glob") == set()
    assert _names(client, "angebot.tmp", mode="exact") == set()
    assert _names(client, r"\.tmp$", mode="regex") == set()


def test_rules_can_be_bypassed_and_deactivated(client):
    _setup(client)
    rule = _add(client, kind="name", pattern="*.tmp")

    # Einmalig übergehen, ohne die Regel anzufassen.
    assert "angebot.tmp" in _names(client, apply_ignores="false")

    # Abschalten wirkt dauerhaft, die Regel bleibt aber erhalten.
    r = client.patch(f"/api/ignores/{rule['id']}", json={"active": False})
    assert r.status_code == 200
    assert "angebot.tmp" in _names(client)
    assert len(client.get("/api/ignores").json()) == 1

    client.patch(f"/api/ignores/{rule['id']}", json={"active": True})
    assert "angebot.tmp" not in _names(client)


def test_rule_can_be_limited_to_one_source(client):
    sid = _setup(client)
    other = client.post(
        "/api/sources", json={"label": "Zweitquelle", "kind": "local"}
    ).json()["id"]
    client.post(
        f"/api/sources/{other}/ingest",
        json={
            "entries": [{"path": "Aktuell/angebot.tmp", "name": "angebot.tmp",
                         "ext": "tmp"}],
            "finalize": True,
            "scan_id": uuid.uuid4().hex,
        },
    )
    _add(client, kind="name", pattern="*.tmp", source_id=sid)

    hits = client.get("/api/search", params={"q": "angebot"}).json()
    sources = {h["source_id"] for h in hits if h["name"] == "angebot.tmp"}
    assert sources == {other}


def test_delete_rule_restores_hits(client):
    _setup(client)
    rule = _add(client, kind="path", pattern="Archiv/2019")
    assert "angebot_alt.pdf" not in _names(client)

    assert client.delete(f"/api/ignores/{rule['id']}").status_code == 204
    assert "angebot_alt.pdf" in _names(client)
    assert client.get("/api/ignores").json() == []


def test_adding_same_rule_twice_reactivates_instead_of_duplicating(client):
    _setup(client)
    first = _add(client, kind="name", pattern="*.tmp")
    client.patch(f"/api/ignores/{first['id']}", json={"active": False})

    again = _add(client, kind="name", pattern="*.tmp")
    assert again["id"] == first["id"]
    assert again["active"] is True
    assert len(client.get("/api/ignores").json()) == 1


def test_path_rule_is_normalized(client):
    _setup(client)
    rule = _add(client, kind="path", pattern="/Archiv/2019/")
    assert rule["pattern"] == "Archiv/2019"
    assert "angebot_alt.pdf" not in _names(client)


def test_rules_are_per_user(client, second_client):
    _setup(client)
    _add(client, kind="name", pattern="*.tmp")
    # Die Regel des einen Nutzers taucht beim anderen nicht auf.
    assert second_client.get("/api/ignores").json() == []


def test_rule_for_foreign_source_is_rejected(client, second_client):
    sid = _setup(client)
    r = second_client.post(
        "/api/ignores", json={"kind": "name", "pattern": "*.tmp", "source_id": sid}
    )
    assert r.status_code == 404


def test_invalid_rule_is_rejected(client):
    _setup(client)
    assert client.post(
        "/api/ignores", json={"kind": "quatsch", "pattern": "x"}
    ).status_code == 422
    assert client.post(
        "/api/ignores", json={"kind": "path", "pattern": "   "}
    ).status_code == 422


def test_rules_do_not_trigger_a_search_on_their_own(client):
    """Ohne Suchtext und ohne Filter bleibt das Ergebnis leer."""
    _setup(client)
    _add(client, kind="name", pattern="*.tmp")
    assert client.get("/api/search", params={"q": ""}).json() == []
