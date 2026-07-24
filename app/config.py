"""Zentrale Konfiguration, aus Umgebungsvariablen ableitbar."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    """Einfache Settings-Klasse (bewusst ohne pydantic-settings gehalten)."""

    def __init__(self) -> None:
        # Pfad zur SQLite-Datei; im Test per Env überschreibbar.
        self.db_path: str = os.environ.get(
            "FTC_DB_PATH", str(BASE_DIR / "filetree_coop.db")
        )
        # Secret für signierte Session-Cookies. In Produktion IMMER setzen!
        self.secret_key: str = os.environ.get(
            "FTC_SECRET_KEY", "dev-secret-change-me"
        )
        # Name des Session-Cookies.
        self.session_cookie: str = os.environ.get(
            "FTC_SESSION_COOKIE", "ftc_session"
        )
        # Cookie nur über HTTPS senden (in Produktion auf true setzen).
        self.session_https_only: bool = (
            os.environ.get("FTC_SESSION_HTTPS_ONLY", "false").lower() == "true"
        )
        # Schlüssel für die Verschleierung gespeicherter LLM-API-Tokens.
        # Standardmäßig aus dem Session-Secret abgeleitet; in Produktion eigenes
        # setzen, sonst hängen Token-Lesbarkeit und Cookie-Secret zusammen.
        self.encryption_key: str = os.environ.get(
            "FTC_ENCRYPTION_KEY", self.secret_key
        )
        # SSRF-Schutz: verbietet LLM-Basis-URLs auf private/lokale Adressen.
        # Für rein lokale Setups (Ollama auf localhost) auf "false" setzen.
        self.llm_block_private_hosts: bool = (
            os.environ.get("FTC_LLM_BLOCK_PRIVATE_HOSTS", "false").lower() == "true"
        )
        # Timeout (Sekunden) für ausgehende LLM-Requests.
        self.llm_timeout_seconds: float = float(
            os.environ.get("FTC_LLM_TIMEOUT_SECONDS", "60")
        )

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
