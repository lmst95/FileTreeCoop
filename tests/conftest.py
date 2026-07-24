"""Test-Setup: frische temporäre SQLite-DB je Testlauf, Helfer-Clients."""

from __future__ import annotations

import os
import tempfile
import uuid

# WICHTIG: DB-Pfad setzen, BEVOR app-Module (und damit die Engine) importiert werden.
_tmp = os.path.join(tempfile.gettempdir(), f"ftc_test_{uuid.uuid4().hex}.db")
os.environ["FTC_DB_PATH"] = _tmp
os.environ["FTC_SECRET_KEY"] = "test-secret"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    init_db()
    yield
    try:
        os.remove(_tmp)
    except OSError:
        pass


def _register(client: TestClient, email: str, name: str, username: str) -> None:
    r = client.post(
        "/api/auth/register",
        json={"email": email, "username": username, "password": "geheim123",
              "display_name": name},
    )
    assert r.status_code == 201, r.text


@pytest.fixture
def client():
    """Eingeloggter Client (frischer Nutzer je Test dank zufälliger Kennung)."""
    c = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    _register(c, f"user_{tag}@example.com", "Testnutzer", f"user{tag}")
    return c


@pytest.fixture
def second_client():
    """Zweiter, separat eingeloggter Nutzer (für Teilen/Übergaben)."""
    c = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    _register(c, f"user_{tag}@example.com", "Kollege", f"kollege{tag}")
    return c
