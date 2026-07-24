"""Gemeinsame Basis für Provider-Adapter.

Ein Provider kapselt genau zwei Fähigkeiten:
- ``chat``        – einen System-/User-Prompt schicken und den Antworttext holen,
- ``list_models`` – verfügbare Modell-IDs auflisten (oder ``None``, wenn der
                    Anbieter das nicht unterstützt -> UI zeigt dann ein Freitextfeld).

Adapter kennen weder ORM noch Features – sie bekommen eine schlanke
``ProviderConfig`` und arbeiten nur mit HTTP.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings


class LLMError(Exception):
    """Nach außen tragbarer Fehler (Netzwerk, Auth, Provider-Antwort).

    ``message`` ist für Endnutzer gedacht; der Router bildet das auf eine
    passende HTTP-Antwort ab.
    """


@dataclass
class ProviderConfig:
    """Alles, was ein Adapter zur Kommunikation braucht – DB-unabhängig."""

    provider_type: str
    base_url: str
    api_key: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    def chat(self, *, system: str, user: str, model: str, params: dict[str, Any]) -> str:
        """Führt eine Completion aus und gibt den reinen Antworttext zurück."""

    @abstractmethod
    def list_models(self) -> list[str] | None:
        """Verfügbare Modell-IDs, oder ``None`` wenn nicht unterstützt."""

    # --- Helfer für Unterklassen --------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=settings.llm_timeout_seconds)

    def _base(self) -> str:
        return (self.config.base_url or "").rstrip("/")

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """HTTP-Aufruf; wirft nur bei Netzfehlern, gibt 4xx/5xx unverändert zurück.

        Nützlich für Adapter, die eine Fehlerantwort auswerten wollen (z. B. um
        einen nicht unterstützten Parameter zu erkennen und den Aufruf anzupassen).
        """
        try:
            with self._client() as client:
                return client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise LLMError("Zeitüberschreitung bei der Anfrage an den Anbieter") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Verbindung zum Anbieter fehlgeschlagen: {exc}") from exc

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Wie ``_send``, wirft aber zusätzlich ``LLMError`` bei HTTP-Fehlern."""
        resp = self._send(method, url, **kwargs)
        if resp.status_code >= 400:
            raise LLMError(_short_http_error(resp))
        return resp


def _error_message(resp: httpx.Response) -> str:
    """Extrahiert die Klartext-Fehlermeldung aus einer Fehlerantwort."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message", "") or ""
        if isinstance(err, str):
            return err
        return data.get("message", "") or data.get("detail", "") or ""
    return ""


def _short_http_error(resp: httpx.Response) -> str:
    """Baut eine knappe, lesbare Fehlermeldung aus einer Fehlerantwort."""
    detail = _error_message(resp)
    prefix = f"Anbieter antwortete mit HTTP {resp.status_code}"
    return f"{prefix}: {detail}" if detail else prefix
