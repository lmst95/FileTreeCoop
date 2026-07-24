"""Tests für Einladungen: Freigabe an unbekannte E-Mail wird bei Registrierung aktiv."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _new_source(client, label="Team"):
    r = client.post("/api/sources", json={"label": label, "kind": "network"})
    assert r.status_code == 201
    return r.json()["id"]


def test_share_to_unknown_email_creates_invite(client):
    sid = _new_source(client)
    r = client.post(
        f"/api/sources/{sid}/shares",
        json={"identifier": "neu@example.com", "permission": "read"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pending"] is True
    assert body["email"] == "neu@example.com"

    shares = client.get(f"/api/sources/{sid}/shares").json()
    assert len(shares) == 1
    assert shares[0]["pending"] is True


def test_unknown_username_still_404(client):
    sid = _new_source(client)
    r = client.post(
        f"/api/sources/{sid}/shares",
        json={"identifier": "gibtesnicht", "permission": "read"},
    )
    assert r.status_code == 404


def test_invite_redeemed_on_register(client):
    sid = _new_source(client)
    tag = uuid.uuid4().hex[:8]
    email = f"invitee_{tag}@example.com"
    r = client.post(
        f"/api/sources/{sid}/shares",
        json={"identifier": email, "permission": "annotate"},
    )
    assert r.status_code == 201

    # Der Eingeladene registriert sich -> Freigabe greift sofort.
    invitee = TestClient(app)
    r = invitee.post("/api/auth/register", json={
        "email": email, "username": f"inv{tag}",
        "password": "geheim123", "display_name": "Eingeladene",
    })
    assert r.status_code == 201, r.text

    sources = invitee.get("/api/sources").json()
    assert [s["id"] for s in sources] == [sid]

    # Einladung ist verbraucht; echte Freigabe steht in der Liste.
    shares = client.get(f"/api/sources/{sid}/shares").json()
    assert len(shares) == 1
    assert shares[0]["pending"] is False
    assert shares[0]["permission"] == "annotate"


def test_invite_can_be_withdrawn(client):
    sid = _new_source(client)
    r = client.post(
        f"/api/sources/{sid}/shares",
        json={"identifier": "weg@example.com", "permission": "read"},
    )
    invite_id = r.json()["invite_id"]
    r = client.delete(f"/api/sources/{sid}/invites/{invite_id}")
    assert r.status_code == 204
    assert client.get(f"/api/sources/{sid}/shares").json() == []


def test_repeated_invite_updates_permission(client):
    sid = _new_source(client)
    client.post(f"/api/sources/{sid}/shares",
                json={"identifier": "x@example.com", "permission": "read"})
    r = client.post(f"/api/sources/{sid}/shares",
                    json={"identifier": "x@example.com", "permission": "annotate"})
    assert r.status_code == 201
    shares = client.get(f"/api/sources/{sid}/shares").json()
    assert len(shares) == 1
    assert shares[0]["permission"] == "annotate"
