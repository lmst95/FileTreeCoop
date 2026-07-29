"""Der Agent: Heartbeat, Sync-Worker je Ordner, Befehlsausführung.

Aufbau:

- **Ein Worker-Thread pro aktivem Ordner.** Er macht beim Start einen Voll-Scan,
  danach im Wechsel: Live-Änderungen melden (aus dem watchdog-Sammler),
  turnusmäßig erneut voll scannen, und – falls eingeschaltet – Inhalts-Hashes
  nachrechnen. Ein Ordner pro Thread, damit ein hängendes Netzlaufwerk die
  anderen nicht mitreißt.
- **Ein Heartbeat-Thread.** Meldet Zustand und Konfiguration, holt Befehle ab
  und führt sie aus (z. B. „Ordner öffnen“).

Die Konfiguration ist zur Laufzeit änderbar: ``reload()`` fährt die Worker
kontrolliert herunter und neu hoch.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .api import Api, ApiError
from .config import Config, FolderConfig
from .hasher import hash_pending
from .opener import reveal
from .scanner import SourceUnreachable, full_scan, stat_entry

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 5
# Nach einem Fehler (Netz weg, Server aus) nicht sofort wieder anrennen.
ERROR_BACKOFF_SECONDS = 60
# Eine Datei, deren Änderungszeit jünger als das ist, wird als „wird gerade noch
# geschrieben“ behandelt und zurückgelegt. Zweiter Riegel neben der Karenzzeit
# des Sammlers, für Programme, die in großen Blöcken schreiben, ohne dass für
# jeden ein Ereignis eintrifft.
FRESH_MTIME_SECONDS = 2.0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class FolderWorker(threading.Thread):
    """Hält eine Quelle aktuell: Voll-Scan, Live-Änderungen, Hashes."""

    def __init__(self, agent: "Agent", folder: FolderConfig):
        super().__init__(daemon=True, name=f"sync-{folder.source_id}")
        self.agent = agent
        self.folder = folder
        self.stop_event = threading.Event()
        # Von außen anstoßbar („Jetzt scannen“ im Tray-Menü).
        self.scan_now = threading.Event()
        self.collector = None
        self.observer = None
        self.state = "gestartet"

    # --- Hilfen -------------------------------------------------------------

    @property
    def root(self) -> Path:
        return Path(self.folder.local_path)

    def _set_state(self, text: str) -> None:
        self.state = text
        self.agent.notify_state_changed()

    def _due_for_scan(self) -> bool:
        if self.folder.last_scan_at is None:
            return True
        try:
            last = datetime.fromisoformat(self.folder.last_scan_at)
        except ValueError:
            return True
        age = (datetime.now(timezone.utc).replace(tzinfo=None) - last).total_seconds()
        return age >= self.folder.scan_interval_minutes * 60

    def _record_error(self, message: str) -> None:
        self.folder.last_error = message[:300]
        self._set_state(f"Fehler: {message[:60]}")
        log.warning("Ordner %s: %s", self.folder.local_path, message)

    def _clear_error(self) -> None:
        if self.folder.last_error:
            self.folder.last_error = ""
            self.agent.save_config()

    # --- Arbeitsschritte ----------------------------------------------------

    def do_full_scan(self) -> None:
        self._set_state("Voll-Scan läuft …")
        result = full_scan(
            self.agent.api,
            self.folder.source_id,
            self.root,
            cancel=self.stop_event,
            on_progress=lambda n: self._set_state(f"Voll-Scan … {n:,} Einträge".replace(",", ".")),
        )
        if result.cancelled:
            return
        self.folder.last_scan_at = _utcnow_iso()
        self._clear_error()
        self.agent.save_config()
        self._set_state("aktuell")
        log.info(
            "Voll-Scan %s: %d Einträge (+%d neu, %d geändert, %d verschwunden)",
            self.folder.source_label, result.total, result.added,
            result.changed, result.missing,
        )

    def do_live_delta(self, paths: set[str]) -> None:
        """Geänderte Pfade melden – vorhandene als Upsert, fehlende als entfernt.

        Ein Live-Delta kennt nur die betroffenen Pfade, nicht den ganzen Baum.
        Deshalb wird nichts finalisiert und Verschwundenes ausdrücklich benannt
        (``removed``) – sonst hielte der Server alles Übrige für gelöscht.

        Hierher kommen nur Pfade, die ihre Karenzzeit hinter sich haben (siehe
        ``watcher.ChangeCollector``). Wer trotzdem noch nach frischer Schreibarbeit
        aussieht, wird zurückgelegt statt halb fertig gemeldet.
        """
        entries: list[dict] = []
        removed: list[str] = []
        still_busy: list[str] = []
        now = time.time()
        for rel in sorted(paths):
            entry = stat_entry(self.root, rel)
            if entry is None:
                removed.append(rel.replace("\\", "/"))
                continue
            # Zweiter Riegel gegen halb geschriebene Dateien: eine Datei, deren
            # mtime gerade eben erst gesetzt wurde, ist vermutlich noch in Arbeit.
            if not entry["is_dir"] and now - entry["mtime"] < FRESH_MTIME_SECONDS:
                still_busy.append(rel)
                continue
            entries.append(entry)
            # Ein neuer Ordner kann komplett mit Inhalt aufgetaucht sein
            # (Kopieren, Entpacken). watchdog meldet dann zwar auch die
            # Kinder, aber nicht verlässlich alle – deshalb sicherheitshalber
            # der nächste Voll-Scan.
            if entry["is_dir"]:
                self.scan_now.set()
        if still_busy and self.collector is not None:
            self.collector.requeue(still_busy)
        if not entries and not removed:
            return
        self._set_state(f"meldet {len(entries) + len(removed)} Änderung(en) …")
        self.agent.api.ingest(
            self.folder.source_id,
            entries,
            scan_id=str(uuid.uuid4()),
            finalize=True,
            kind="live",
            removed=removed,
            # Live-Deltas dürfen niemals aufräumen – sie kennen den Baum nicht.
            mark_missing=False,
        )
        self._clear_error()
        self._set_state("aktuell")

    def do_hashing(self) -> None:
        self._set_state("Inhalts-Hashes …")
        result = hash_pending(
            self.agent.api,
            self.folder.source_id,
            self.root,
            cancel=self.stop_event,
            on_progress=lambda r: self._set_state(
                f"Hashes … {r.hashed:,}".replace(",", ".")
            ),
        )
        if not result.cancelled:
            log.info(
                "Hashes %s: %d berechnet, %d Fehler, %d wiedererkannt",
                self.folder.source_label, result.hashed, result.errors,
                result.reconciled,
            )
        self._set_state("aktuell")

    # --- Hauptschleife ------------------------------------------------------

    def run(self) -> None:
        if not self.folder.is_ready():
            self._record_error("Ordner nicht gefunden")
            return

        if self.folder.watch_enabled:
            try:
                from .watcher import ChangeCollector, start_observer

                self.collector = ChangeCollector(self.root, self.folder.settle_seconds)
                self.observer = start_observer(self.root, self.collector)
            except Exception as e:  # watchdog kann je nach System zicken
                self._record_error(f"Live-Überwachung nicht möglich: {e}")
                self.collector = None

        while not self.stop_event.is_set():
            try:
                if self.agent.paused:
                    self._set_state("pausiert")
                    self.stop_event.wait(2)
                    continue

                if self.scan_now.is_set() or self._due_for_scan():
                    self.scan_now.clear()
                    self.do_full_scan()
                    if self.stop_event.is_set():
                        break
                    if self.folder.hash_enabled:
                        self.do_hashing()

                # Live-Änderungen: der Sammler gibt nur Pfade heraus, die ihre
                # Karenzzeit hinter sich haben. Geschlafen wird genau so lange,
                # bis der nächste reif ist – kein Pollen im Sekundentakt.
                if self.collector is not None:
                    ready = self.collector.take_settled()
                    if ready:
                        self.do_live_delta(ready)
                        continue
                    wait = self.collector.seconds_until_next()
                    if wait is None:
                        self.collector.event.wait(1)
                    else:
                        self.stop_event.wait(min(max(wait, 0.2), 5))
                else:
                    self.stop_event.wait(5)
            except SourceUnreachable as e:
                self._record_error(f"Ordner nicht erreichbar: {e}")
                self.stop_event.wait(ERROR_BACKOFF_SECONDS)
            except ApiError as e:
                self._record_error(str(e))
                self.stop_event.wait(ERROR_BACKOFF_SECONDS)
            except Exception as e:  # ein Bug hier darf den Client nicht killen
                log.exception("Unerwarteter Fehler im Worker")
                self._record_error(str(e))
                self.stop_event.wait(ERROR_BACKOFF_SECONDS)

        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=5)
        self.state = "gestoppt"

    def stop(self) -> None:
        self.stop_event.set()


class Agent:
    """Bündelt Konfiguration, Server-Anbindung und die laufenden Worker."""

    def __init__(self, config: Config):
        self.config = config
        self.api = Api(config.server_url, config.token)
        self.workers: list[FolderWorker] = []
        self.paused = False
        self.online = False
        self.last_error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        # Wird gesetzt, wenn sich der Zustand ändert (Tray-Tooltip aktualisieren).
        self.on_state_changed = None

    # --- Zustand ------------------------------------------------------------

    def notify_state_changed(self) -> None:
        if self.on_state_changed:
            try:
                self.on_state_changed()
            except Exception:
                log.exception("Zustands-Callback fehlgeschlagen")

    def status_text(self) -> str:
        if not self.config.is_connected():
            return "nicht verbunden"
        if self.paused:
            return "pausiert"
        active = [w for w in self.workers if w.is_alive()]
        if not active:
            return "kein Ordner eingerichtet"
        busy = [w for w in active if w.state not in {"aktuell", "gestartet"}]
        if busy:
            return f"{busy[0].folder.source_label}: {busy[0].state}"
        errors = [w for w in active if w.folder.last_error]
        if errors:
            return f"{len(errors)} Ordner mit Problemen"
        return f"{len(active)} Ordner aktuell"

    def save_config(self) -> None:
        with self._lock:
            try:
                self.config.save()
            except OSError as e:
                log.warning("Konfiguration konnte nicht gespeichert werden: %s", e)

    # --- Lebenszyklus -------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self.api.server_url = (self.config.server_url or "").rstrip("/")
        self.api.token = self.config.token
        self._start_workers()
        if self._heartbeat_thread is None or not self._heartbeat_thread.is_alive():
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True, name="heartbeat"
            )
            self._heartbeat_thread.start()

    def _start_workers(self) -> None:
        if not self.config.is_connected():
            return
        for folder in self.config.active_folders():
            worker = FolderWorker(self, folder)
            self.workers.append(worker)
            worker.start()

    def _stop_workers(self) -> None:
        for worker in self.workers:
            worker.stop()
        for worker in self.workers:
            # Ein Worker kann in einem langen Scan stecken; wir warten begrenzt
            # und lassen ihn sonst als Daemon-Thread auslaufen.
            worker.join(timeout=10)
        self.workers = []

    def reload(self, config: Config | None = None) -> None:
        """Konfiguration übernehmen und die Worker neu aufsetzen."""
        self._stop_workers()
        if config is not None:
            self.config = config
        self.api.server_url = (self.config.server_url or "").rstrip("/")
        self.api.token = self.config.token
        self._start_workers()
        self.notify_state_changed()

    def stop(self) -> None:
        self._stop.set()
        self._stop_workers()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.notify_state_changed()

    def scan_all_now(self) -> None:
        for worker in self.workers:
            worker.scan_now.set()

    # --- Heartbeat + Befehle ------------------------------------------------

    def _folders_payload(self) -> list[dict]:
        return [
            {
                "source_id": f.source_id,
                "local_path": f.local_path,
                "enabled": f.enabled,
                "hash_enabled": f.hash_enabled,
                "watch_enabled": f.watch_enabled,
                "scan_interval_minutes": f.scan_interval_minutes,
                "last_scan_at": f.last_scan_at,
                "last_error": f.last_error,
            }
            for f in self.config.folders
        ]

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            if not self.config.is_connected():
                self.online = False
                continue
            try:
                res = self.api.heartbeat(
                    self.status_text(), self._folders_payload(), self.config.client_name
                )
                was_online = self.online
                self.online = True
                self.last_error = ""
                if not was_online:
                    self.notify_state_changed()
                # Aus der Ferne pausiert? (Schalter auf der Geräte-Seite.)
                if res.get("paused") != self.paused:
                    self.set_paused(bool(res.get("paused")))
                for cmd in res.get("commands", []):
                    self._run_command(cmd)
            except ApiError as e:
                if self.online:
                    self.notify_state_changed()
                self.online = False
                self.last_error = str(e)
                log.debug("Heartbeat fehlgeschlagen: %s", e)

    def _run_command(self, cmd: dict) -> None:
        command = cmd.get("command")
        payload = cmd.get("payload") or {}
        try:
            if command == "open_folder":
                self._open_folder(payload)
            elif command == "rescan":
                self.scan_all_now()
            else:
                raise ValueError(f"Unbekannter Befehl: {command}")
            self.api.ack_command(cmd["id"], "done")
        except Exception as e:
            log.warning("Befehl %s fehlgeschlagen: %s", command, e)
            try:
                self.api.ack_command(cmd["id"], "error", str(e))
            except ApiError:
                pass

    def _open_folder(self, payload: dict) -> None:
        folder = self.config.folder(int(payload.get("source_id", 0)))
        if folder is None or not folder.local_path:
            raise ValueError("Diese Quelle ist auf diesem Gerät nicht eingerichtet.")
        rel = (payload.get("path") or "").strip("/")
        target = Path(folder.local_path)
        if rel:
            # Nur innerhalb der eingerichteten Wurzel öffnen: der Pfad kommt vom
            # Server, und ein „..“ darin dürfte nie aus dem Ordner herausführen.
            target = (target / rel).resolve()
            root = Path(folder.local_path).resolve()
            if root != target and root not in target.parents:
                raise ValueError("Pfad liegt außerhalb des eingerichteten Ordners.")
        reveal(target, bool(payload.get("is_dir", True)))
        log.info("Geöffnet: %s", target)
