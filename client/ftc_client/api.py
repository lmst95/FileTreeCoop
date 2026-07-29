"""HTTP-Anbindung an den filetree_coop-Server.

Der Client nutzt für das eigentliche Melden von Dateien exakt dieselben
Endpunkte wie der Browser-Scanner (``/api/sources/{id}/ingest``, ``/hash-todo``,
``/hashes``); nur die Authentifizierung unterscheidet sich: statt eines
Session-Cookies schickt er ``Authorization: Bearer <Gerätetoken>``.
"""

from __future__ import annotations

import platform
import sys

import requests

from . import __version__

# Großzügig, aber nicht unendlich: ein hängendes Netzlaufwerk soll den
# Sync-Thread nicht für immer blockieren.
TIMEOUT = (10, 120)  # (connect, read)


class ApiError(RuntimeError):
    """Fehler vom Server – mit der Meldung, die der Server geliefert hat."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Api:
    def __init__(self, server_url: str = "", token: str = ""):
        self.server_url = (server_url or "").rstrip("/")
        self.token = token or ""
        self._session = requests.Session()

    # --- Basis --------------------------------------------------------------

    def _url(self, path: str) -> str:
        if not self.server_url:
            raise ApiError("Keine Serveradresse eingestellt.")
        return f"{self.server_url}{path}"

    def _request(self, method: str, path: str, *, auth: bool = True, **kwargs):
        headers = kwargs.pop("headers", {})
        if auth:
            if not self.token:
                raise ApiError("Nicht mit dem Server verbunden.")
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            res = self._session.request(
                method, self._url(path), headers=headers, timeout=TIMEOUT, **kwargs
            )
        except requests.RequestException as e:
            raise ApiError(f"Server nicht erreichbar: {e}") from e

        if res.status_code == 204:
            return None
        detail = None
        try:
            data = res.json()
        except ValueError:
            data = None
            detail = res.text[:300]
        if not res.ok:
            if isinstance(data, dict):
                detail = data.get("detail") or detail
            raise ApiError(
                str(detail or f"{res.status_code} {res.reason}"), res.status_code
            )
        return data

    # --- Registrierung ------------------------------------------------------

    def register(self, identifier: str, password: str, name: str) -> dict:
        """Konto-Daten einmalig gegen einen Gerätetoken tauschen."""
        data = self._request(
            "POST",
            "/api/clients/register",
            auth=False,
            json={
                "identifier": identifier,
                "password": password,
                "name": name,
                "hostname": platform.node()[:200],
                "platform": sys.platform[:40],
                "version": __version__,
            },
        )
        self.token = data["token"]
        return data

    # --- Heartbeat + Befehle ------------------------------------------------

    def heartbeat(self, status_text: str, folders: list[dict], name: str = "") -> dict:
        return self._request(
            "POST",
            "/api/clients/heartbeat",
            json={
                "version": __version__,
                "status_text": status_text[:300],
                "name": name or None,
                "hostname": platform.node()[:200],
                "folders": folders,
            },
        )

    def ack_command(self, command_id: int, status: str, result: str = "") -> None:
        self._request(
            "POST",
            f"/api/clients/commands/{command_id}/ack",
            json={"status": status, "result": result[:500]},
        )

    def unregister(self) -> None:
        self._request("POST", "/api/clients/unregister")

    # --- Quellen ------------------------------------------------------------

    def list_sources(self) -> list[dict]:
        return self._request("GET", "/api/sources") or []

    def create_source(self, label: str, kind: str = "local", host_hint: str = "") -> dict:
        return self._request(
            "POST",
            "/api/sources",
            json={"label": label, "kind": kind, "host_hint": host_hint},
        )

    # --- Index abgleichen ---------------------------------------------------

    def ingest(
        self,
        source_id: int,
        entries: list[dict],
        *,
        scan_id: str,
        finalize: bool = False,
        kind: str = "full",
        removed: list[str] | None = None,
        skipped: list[dict] | None = None,
        mark_missing: bool = True,
    ) -> dict:
        body = {
            "entries": entries,
            "scan_id": scan_id,
            "finalize": finalize,
            "kind": kind,
            "mark_missing": mark_missing,
        }
        if removed:
            body["removed"] = removed
        if skipped:
            body["skipped"] = skipped
        return self._request("POST", f"/api/sources/{source_id}/ingest", json=body)

    def hash_todo(self, source_id: int, limit: int = 5000) -> list[dict]:
        return (
            self._request(
                "GET", f"/api/sources/{source_id}/hash-todo", params={"limit": limit}
            )
            or []
        )

    def submit_hashes(self, source_id: int, items: list[dict]) -> dict:
        return self._request(
            "POST", f"/api/sources/{source_id}/hashes", json={"items": items}
        )

    def hash_summary(self, source_id: int) -> dict:
        return self._request("GET", f"/api/sources/{source_id}/hash-summary") or {}
