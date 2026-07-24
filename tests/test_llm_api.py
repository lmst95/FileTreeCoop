"""API-Tests für /api/llm: CRUD, Feature-Zuordnung, generischer /run.

Ausgehende HTTP-Calls werden über einen gepatchten Provider-Client gemockt –
es gehen keine echten Netz-Requests raus.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm.defaults import DEFAULT_PROMPTS
from app.llm.providers.base import Provider

DEFAULT_PROMPT_NAMES = {d.name for d in DEFAULT_PROMPTS}


@pytest.fixture
def mock_llm(monkeypatch):
    """Patcht den Provider-HTTP-Client auf einen MockTransport (OpenAI-Form)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            user_msg = body["messages"][-1]["content"]
            return httpx.Response(200, json={
                "choices": [{"message": {"content": f"[refined] {user_msg}"}}]
            })
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]
            })
        return httpx.Response(404, json={"error": {"message": "not found"}})

    def _client(self):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)

    monkeypatch.setattr(Provider, "_client", _client)


def _make_connection(client, **over):
    payload = {
        "label": "OpenAI Test",
        "provider_type": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-secret-9999",
        "default_model": "gpt-4o",
    }
    payload.update(over)
    r = client.post("/api/llm/connections", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_meta(client):
    r = client.get("/api/llm/meta")
    assert r.status_code == 200
    data = r.json()
    assert any(pt["value"] == "openai" for pt in data["provider_types"])
    assert any(f["key"] == "notes" for f in data["features"])


def test_connection_crud_and_key_never_leaked(client):
    conn = _make_connection(client)
    assert conn["has_key"] is True
    assert conn["key_hint"].endswith("9999")
    assert "api_key" not in conn and "api_key_enc" not in conn

    # Liste
    r = client.get("/api/llm/connections")
    assert r.status_code == 200 and len(r.json()) == 1

    # Patch Label, Token unverändert lassen
    r = client.patch(f"/api/llm/connections/{conn['id']}", json={"label": "Neu"})
    assert r.status_code == 200
    assert r.json()["label"] == "Neu"
    assert r.json()["has_key"] is True

    # Token löschen via leerem String
    r = client.patch(f"/api/llm/connections/{conn['id']}", json={"api_key": ""})
    assert r.json()["has_key"] is False

    # Löschen
    r = client.delete(f"/api/llm/connections/{conn['id']}")
    assert r.status_code == 204
    assert client.get("/api/llm/connections").json() == []


def test_connection_test_and_models(client, mock_llm):
    conn = _make_connection(client)
    r = client.post(f"/api/llm/connections/{conn['id']}/test")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["models_count"] == 2

    r = client.post(f"/api/llm/connections/{conn['id']}/models")
    assert r.status_code == 200
    body = r.json()
    assert body["supported"] is True
    assert "gpt-4o" in body["models"]

    # Cache landet in der Verbindungs-Ausgabe
    conn2 = client.get("/api/llm/connections").json()[0]
    assert "gpt-4o" in conn2["models"]
    assert conn2["models_fetched_at"]


def test_setting_and_prompt_with_features(client):
    conn = _make_connection(client)
    r = client.post("/api/llm/settings", json={
        "label": "Standard", "connection_id": conn["id"], "model": "gpt-4o",
        "system_prompt": "Sei praezise.", "params": {"temperature": 0.3},
        "features": ["notes"],
    })
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["features"] == ["notes"]
    assert s["params"]["temperature"] == 0.3
    assert s["connection_label"] == "OpenAI Test"

    r = client.post("/api/llm/prompts", json={
        "name": "Korrektur", "body": "Korrigiere: {{input}}", "features": ["notes"],
    })
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["features"] == ["notes"]

    # Feature-Optionen liefern das Setting + den neuen Prompt (neben den Defaults)
    r = client.get("/api/llm/features/notes")
    opts = r.json()
    assert len(opts["settings"]) == 1
    prompt_names = {p["name"] for p in opts["prompts"]}
    assert "Korrektur" in prompt_names
    assert len(opts["prompts"]) == len(DEFAULT_PROMPTS) + 1

    # Unbekanntes Feature -> 422 beim Anlegen
    r = client.post("/api/llm/prompts", json={"name": "x", "features": ["bogus"]})
    assert r.status_code == 422


def test_default_prompts_seeded_on_register(client):
    """Neue Nutzer bekommen die Standard-Prompts (Feature „notes“, mit Platzhalter)."""
    prompts = client.get("/api/llm/prompts").json()
    by_name = {p["name"]: p for p in prompts}
    assert DEFAULT_PROMPT_NAMES <= set(by_name)
    for name in DEFAULT_PROMPT_NAMES:
        assert "notes" in by_name[name]["features"]
        assert "{{input}}" in by_name[name]["body"]

    # Und damit sofort im Notizen-Feature auswählbar.
    opts = client.get("/api/llm/features/notes").json()
    assert DEFAULT_PROMPT_NAMES <= {p["name"] for p in opts["prompts"]}


def test_default_prompts_endpoint_idempotent(client):
    before = client.get("/api/llm/prompts").json()
    r = client.post("/api/llm/prompts/defaults")
    assert r.status_code == 201, r.text
    assert {p["name"] for p in r.json()} == DEFAULT_PROMPT_NAMES
    # Erneutes Anlegen dupliziert nichts.
    after = client.get("/api/llm/prompts").json()
    assert len(after) == len(before)

    # Nach dem Löschen legt der Endpunkt die fehlende Vorlage wieder an.
    victim = next(p for p in after if p["name"] in DEFAULT_PROMPT_NAMES)
    assert client.delete(f"/api/llm/prompts/{victim['id']}").status_code == 204
    client.post("/api/llm/prompts/defaults")
    restored = client.get("/api/llm/prompts").json()
    assert len(restored) == len(after)
    assert DEFAULT_PROMPT_NAMES <= {p["name"] for p in restored}


def test_web_search_setting_ready_to_use(client):
    """Ein-Klick-Setting: bindet Web-Suche + Suchmodell an die OpenAI-Verbindung."""
    conn = _make_connection(client)  # provider_type "openai"
    r = client.post("/api/llm/settings/web-search")
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["params"].get("web_search") is True
    assert s["model"]  # ein suchfähiges Modell voreingestellt
    assert s["connection_id"] == conn["id"]
    assert "notes" in s["features"]  # sofort im Notizen-Feature wählbar

    # Idempotent: erneuter Klick legt kein zweites Setting an.
    assert client.post("/api/llm/settings/web-search").status_code == 201
    assert len(client.get("/api/llm/settings").json()) == 1


def test_web_search_setting_needs_openai_connection(client):
    """Ohne OpenAI-Verbindung gibt es einen sprechenden 409 statt eines Settings."""
    _make_connection(
        client, label="Ollama", provider_type="ollama",
        base_url="http://localhost:11434/v1", api_key=None,
    )
    r = client.post("/api/llm/settings/web-search")
    assert r.status_code == 409
    assert "OpenAI" in r.json()["detail"]
    assert client.get("/api/llm/settings").json() == []


def test_run_persists_and_history(client, mock_llm):
    conn = _make_connection(client)
    s = client.post("/api/llm/settings", json={
        "label": "S", "connection_id": conn["id"], "model": "gpt-4o", "features": ["notes"],
    }).json()
    p = client.post("/api/llm/prompts", json={
        "name": "P", "body": "Verbessere: {{input}}", "features": ["notes"],
    }).json()

    r = client.post("/api/llm/run", json={
        "setting_id": s["id"], "prompt_id": p["id"],
        "input_text": "hallo welt", "target_kind": "annotation", "target_id": 42,
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["output_text"] == "[refined] Verbessere: hallo welt"
    assert out["run_id"] and out["model"] == "gpt-4o"

    # Verlauf fuer das Ziel
    r = client.get("/api/llm/runs?target_kind=annotation&target_id=42")
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["output_text"] == "[refined] Verbessere: hallo welt"
    assert runs[0]["meta"]["prompt_name"] == "P"


def test_web_search_sends_option_and_appends_sources(client, monkeypatch):
    """Bei web_search=True: web_search_options in der Payload + Quellen im Output."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            captured["web_search_options"] = body.get("web_search_options")
            return httpx.Response(200, json={
                "choices": [{"message": {
                    "content": "Heute gab es gute Nachrichten.",
                    "annotations": [
                        {"type": "url_citation", "url_citation": {
                            "url": "https://example.com/a", "title": "Quelle A"}},
                        # Duplikat derselben URL -> nur einmal in den Quellen.
                        {"type": "url_citation", "url_citation": {
                            "url": "https://example.com/a", "title": "Dublette"}},
                        {"type": "url_citation", "url_citation": {
                            "url": "https://example.com/b", "title": "Quelle B"}},
                    ],
                }}]
            })
        return httpx.Response(404, json={"error": {"message": "not found"}})

    def _client(self):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    monkeypatch.setattr(Provider, "_client", _client)

    conn = _make_connection(client)
    s = client.post("/api/llm/settings", json={
        "label": "Websuche", "connection_id": conn["id"],
        "model": "gpt-4o-search-preview", "params": {"web_search": True},
    }).json()

    r = client.post("/api/llm/run", json={
        "setting_id": s["id"], "input_text": "Gute Nachrichten heute?",
    })
    assert r.status_code == 200, r.text
    out = r.json()["output_text"]
    assert "Heute gab es gute Nachrichten." in out
    assert "Quellen:" in out
    assert "https://example.com/a" in out and "https://example.com/b" in out
    assert out.count("https://example.com/a") == 1  # dedupliziert
    assert captured["web_search_options"] == {}


def test_no_web_search_option_when_disabled(client, monkeypatch):
    """Ohne web_search-Flag bleibt web_search_options aus der Payload."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["has_option"] = "web_search_options" in body
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}]
        })

    def _client(self):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    monkeypatch.setattr(Provider, "_client", _client)

    conn = _make_connection(client)
    s = client.post("/api/llm/settings", json={
        "label": "Ohne", "connection_id": conn["id"], "model": "gpt-4o",
    }).json()
    r = client.post("/api/llm/run", json={"setting_id": s["id"], "input_text": "hi"})
    assert r.status_code == 200, r.text
    assert captured["has_option"] is False


def test_run_error_is_recorded(client, monkeypatch):
    conn = _make_connection(client)
    s = client.post("/api/llm/settings", json={
        "label": "S", "connection_id": conn["id"], "model": "gpt-4o",
    }).json()

    def failing(self):
        def handler(_req):
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    monkeypatch.setattr(Provider, "_client", failing)

    r = client.post("/api/llm/run", json={
        "setting_id": s["id"], "input_text": "x", "target_kind": "annotation", "target_id": 7,
    })
    assert r.status_code == 502
    # Fehl-Lauf wurde protokolliert
    runs = client.get("/api/llm/runs?target_kind=annotation&target_id=7").json()
    assert len(runs) == 1 and runs[0]["status"] == "error"
    assert "bad key" in runs[0]["error"]


def test_ownership_isolation(client, second_client):
    conn = _make_connection(client)
    # Zweiter Nutzer sieht die Verbindung nicht und darf nicht zugreifen.
    assert second_client.get("/api/llm/connections").json() == []
    assert second_client.patch(
        f"/api/llm/connections/{conn['id']}", json={"label": "x"}
    ).status_code == 404
    assert second_client.delete(
        f"/api/llm/connections/{conn['id']}"
    ).status_code == 404
