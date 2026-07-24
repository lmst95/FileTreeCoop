"""Tests für Username-Registrierung, Login per E-Mail/Username und Teilen."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _reg(c, email, username, name="Nutzer"):
    return c.post("/api/auth/register", json={
        "email": email, "username": username, "password": "geheim123", "display_name": name})


def test_register_returns_username_lowercased():
    c = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    r = _reg(c, f"a_{tag}@x.de", f"MixedCase{tag}")
    assert r.status_code == 201
    assert r.json()["username"] == f"mixedcase{tag}"


def test_login_by_username_and_email():
    c = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    email, username = f"b_{tag}@x.de", f"bob{tag}"
    _reg(c, email, username)

    assert c.post("/api/auth/login", json={"identifier": username, "password": "geheim123"}).status_code == 200
    assert c.post("/api/auth/login", json={"identifier": email, "password": "geheim123"}).status_code == 200
    # Groß-/Kleinschreibung egal.
    assert c.post("/api/auth/login", json={"identifier": username.upper(), "password": "geheim123"}).status_code == 200
    assert c.post("/api/auth/login", json={"identifier": username, "password": "falsch"}).status_code == 401


def test_duplicate_username_and_email_rejected():
    c = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    _reg(c, f"c_{tag}@x.de", f"carol{tag}")
    assert _reg(c, f"other_{tag}@x.de", f"carol{tag}").status_code == 409  # username
    assert _reg(c, f"c_{tag}@x.de", f"other{tag}").status_code == 409       # email


def test_invalid_username_rejected():
    c = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    assert _reg(c, f"d_{tag}@x.de", "ab").status_code == 422          # zu kurz
    assert _reg(c, f"e_{tag}@x.de", "hat leer").status_code == 422    # Leerzeichen
    assert _reg(c, f"f_{tag}@x.de", "mit@zeichen").status_code == 422 # ungültig


def test_share_by_username():
    owner = TestClient(app)
    colleague = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    _reg(owner, f"own_{tag}@x.de", f"owner{tag}")
    _reg(colleague, f"kol_{tag}@x.de", f"kol{tag}")

    sid = owner.post("/api/sources", json={"label": "Q", "kind": "local"}).json()["id"]
    # Freigabe per Username statt E-Mail.
    r = owner.post(f"/api/sources/{sid}/shares",
                   json={"identifier": f"kol{tag}", "permission": "annotate"})
    assert r.status_code == 201, r.text
    assert r.json()["username"] == f"kol{tag}"
    assert sid in [s["id"] for s in colleague.get("/api/sources").json()]


def test_share_unknown_identifier_404():
    owner = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    _reg(owner, f"o2_{tag}@x.de", f"owner2{tag}")
    sid = owner.post("/api/sources", json={"label": "Q", "kind": "local"}).json()["id"]
    r = owner.post(f"/api/sources/{sid}/shares",
                   json={"identifier": "gibtsnicht", "permission": "read"})
    assert r.status_code == 404
