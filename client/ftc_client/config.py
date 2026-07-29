"""Konfiguration des Clients: laden, speichern, Voreinstellungen.

Die Konfiguration liegt als JSON im Konfigurationsverzeichnis des Betriebs-
systems und ist die **maßgebliche** Fassung: welche Ordner überwacht werden,
weiß allein der Client (nur er kennt seine lokalen Pfade). Dem Server meldet er
sie beim Heartbeat, damit die Weboberfläche anzeigen kann, welches Gerät welche
Quelle betreut – aber gesteuert wird hier.

Im selben JSON steht der Gerätetoken. Das Konto-Passwort wird **nie**
gespeichert: es wird einmal gegen den Token getauscht und danach vergessen.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import APP_NAME


def config_dir() -> Path:
    """Konfigurationsverzeichnis nach Konvention des jeweiligen Systems."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_NAME


def config_path() -> Path:
    return config_dir() / "client.json"


def log_path() -> Path:
    return config_dir() / "client.log"


@dataclass
class FolderConfig:
    """Ein überwachter Ordner, gebunden an eine Quelle auf dem Server."""

    source_id: int
    source_label: str = ""
    local_path: str = ""
    # Sync dieser Quelle überhaupt aktiv? (Aus = der Ordner wird ignoriert.)
    enabled: bool = True
    # Inhalts-Hashes im Hintergrund berechnen. Bewusst getrennt schaltbar: der
    # Index aktuell zu halten kostet fast nichts, Hashen liest jede Datei ganz.
    hash_enabled: bool = False
    # Live-Überwachung per watchdog zusätzlich zum periodischen Voll-Scan.
    watch_enabled: bool = True
    # Karenzzeit je erkannter Änderung: erst wenn ein Pfad so lange Ruhe gegeben
    # hat, wird er gemeldet. Verhindert, dass halb geschriebene Dateien und
    # kurzlebige Temporärdateien im Index landen (siehe watcher.py).
    settle_seconds: int = 10
    scan_interval_minutes: int = 60
    # Laufzeit-Zustand, wird mitgespeichert, damit ein Neustart nicht sofort
    # wieder alles scannt.
    last_scan_at: str | None = None
    last_error: str = ""

    def is_ready(self) -> bool:
        return bool(self.local_path) and Path(self.local_path).is_dir()


@dataclass
class Config:
    server_url: str = ""
    token: str = ""
    client_id: int | None = None
    client_name: str = ""
    # Nur zur Anzeige („angemeldet als …“); keine Berechtigung hängt daran.
    user_display: str = ""
    folders: list[FolderConfig] = field(default_factory=list)
    autostart: bool = False
    # Beim Programmstart direkt das Einstellungsfenster zeigen? Nach der
    # Ersteinrichtung normalerweise aus – der Client soll still starten.
    show_settings_on_start: bool = True

    # --- Laden / Speichern --------------------------------------------------

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            return cls(client_name=default_client_name())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Kaputte Datei darf den Start nicht verhindern – lieber mit
            # Voreinstellungen hochkommen, der Nutzer sieht das im Fenster.
            return cls(client_name=default_client_name())
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        folder_known = {f.name for f in fields(FolderConfig)}
        data["folders"] = [
            FolderConfig(**{k: v for k, v in f.items() if k in folder_known})
            for f in raw.get("folders", [])
            if isinstance(f, dict) and "source_id" in f
        ]
        cfg = cls(**data)
        if not cfg.client_name:
            cfg.client_name = default_client_name()
        return cfg

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Erst in eine Nebendatei schreiben, dann umbenennen: ein Absturz
        # mittendrin darf keine halbe Konfiguration hinterlassen.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, path)
        _restrict_permissions(path)

    # --- Bequemlichkeit -----------------------------------------------------

    def folder(self, source_id: int) -> FolderConfig | None:
        for f in self.folders:
            if f.source_id == source_id:
                return f
        return None

    def active_folders(self) -> list[FolderConfig]:
        return [f for f in self.folders if f.enabled and f.local_path]

    def is_connected(self) -> bool:
        return bool(self.server_url and self.token)


def _restrict_permissions(path: Path) -> None:
    """Die Datei enthält den Gerätetoken – auf Unix nur für den Besitzer.

    Unter Windows erbt die Datei die Rechte des Nutzerprofils; ein eigener
    ACL-Eingriff wäre dort mehr Risiko als Gewinn.
    """
    if sys.platform != "win32":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def default_client_name() -> str:
    try:
        return socket.gethostname() or "Desktop-Client"
    except OSError:
        return "Desktop-Client"
