"""Symbol im Infobereich der Taskleiste (unten rechts) samt Menü.

Threading-Aufteilung – der Grund, warum das hier so aussieht:

- **tkinter** verträgt Aufrufe nur aus dem Thread, in dem seine Schleife läuft.
  Deshalb läuft die Tk-Hauptschleife im *Haupt*-Thread (mit unsichtbarem
  Fenster), und alles, was das Einstellungsfenster betrifft, wird per
  ``root.after`` dorthin geschickt.
- **pystray** läuft entsprechend in einem eigenen Thread. Unter Windows und
  Linux ist das zulässig; unter macOS verlangt pystray den Haupt-Thread – dort
  bleibt das Menü deshalb auf das Nötigste beschränkt (siehe README).
"""

from __future__ import annotations

import logging
import threading

import pystray
from pystray import MenuItem

from . import __version__
from .agent import Agent
from .icon import make_icon

log = logging.getLogger(__name__)


# Wie oft das Symbol höchstens angefasst wird. Zustandsmeldungen kommen im
# 400-ms-Takt (Hash-Fortschritt); jede davon in einen Win32-Aufruf zu übersetzen
# wäre Verschwendung und sähe nur nach Flackern aus.
MIN_REFRESH_SECONDS = 0.75


class Tray:
    """Taskleisten-Symbol.

    **Alle** Zugriffe auf ``pystray`` laufen über genau einen Thread (siehe
    ``_refresh_loop``). Das ist keine Stilfrage: ``pystray._win32`` gibt beim
    Symbolwechsel erst das alte Icon-Handle frei und erzeugt dann ein neues.
    Rufen zwei Threads das gleichzeitig auf – und genau das passiert, denn jeder
    Sync-Worker und der Heartbeat melden Zustandsänderungen –, dann zerstört der
    zweite ein bereits freigegebenes Handle: ``WinError 1402``.

    ``refresh()`` setzt deshalb nur eine Flagge; angefasst wird das Symbol
    ausschließlich im Refresh-Thread, und auch dort nur, wenn sich am Ergebnis
    wirklich etwas geändert hat.
    """

    def __init__(self, agent: Agent, on_settings, on_quit):
        self.agent = agent
        self.on_settings = on_settings
        self.on_quit = on_quit
        self._icon = pystray.Icon(
            "filetree_coop",
            make_icon(self._state()),
            self._tooltip(),
            menu=self._build_menu(),
        )
        self._thread: threading.Thread | None = None
        self._refresh_thread: threading.Thread | None = None
        self._dirty = threading.Event()
        self._stopping = threading.Event()
        # Zuletzt *tatsächlich* gesetzte Werte – Grundlage dafür, unnötige
        # Win32-Aufrufe zu vermeiden.
        self._shown_state = self._state()
        self._shown_title = self._tooltip()
        self._shown_menu = self._menu_signature()

    # --- Zustand ------------------------------------------------------------

    def _state(self) -> str:
        if not self.agent.config.is_connected():
            return "offline"
        if self.agent.paused:
            return "paused"
        if not self.agent.online:
            return "offline"
        # Momentaufnahme: die Liste wird beim Neuladen der Konfiguration aus
        # einem anderen Thread ausgetauscht.
        workers = list(self.agent.workers)
        if any(w.folder.last_error for w in workers):
            return "error"
        if any(w.state not in {"aktuell", "gestartet", "gestoppt"} for w in workers):
            return "busy"
        return "ok"

    def _tooltip(self) -> str:
        return f"filetree_coop · {self.agent.status_text()}"

    def _menu_signature(self) -> tuple:
        """Woran man erkennt, dass das Menü neu gebaut werden muss."""
        return (
            self.agent.status_text(),
            self.agent.paused,
            tuple(
                (w.folder.source_label, w.state, w.folder.last_error)
                for w in list(self.agent.workers)
            ),
        )

    def refresh(self) -> None:
        """Aktualisierung anfordern – aus jedem Thread gefahrlos aufrufbar.

        Tut selbst nichts am Symbol (siehe Klassen-Doku), sondern weckt nur den
        Refresh-Thread.
        """
        self._dirty.set()

    def _refresh_loop(self) -> None:
        while not self._stopping.is_set():
            # Wartet auf eine Meldung, schaut aber auch ohne regelmäßig nach:
            # „vor 4 min gesehen“ im Tooltip altert auch ohne Ereignis.
            self._dirty.wait(2.0)
            if self._stopping.is_set():
                break
            self._dirty.clear()
            self._apply()
            # Kurz sperren, damit eine Salve von Meldungen zu einer
            # Aktualisierung zusammenfällt.
            self._stopping.wait(MIN_REFRESH_SECONDS)

    def _apply(self) -> None:
        try:
            state = self._state()
            if state != self._shown_state:
                # Der teure und der heikle Aufruf: hier wird das alte Handle
                # freigegeben. Nur bei echtem Farbwechsel.
                self._icon.icon = make_icon(state)
                self._shown_state = state

            title = self._tooltip()
            if title != self._shown_title:
                self._icon.title = title
                self._shown_title = title

            signature = self._menu_signature()
            if signature != self._shown_menu:
                self._icon.menu = self._build_menu()
                self._icon.update_menu()
                self._shown_menu = signature
        except Exception:
            # Ein Anzeigeproblem darf den Sync nicht stören.
            log.debug("Tray-Aktualisierung fehlgeschlagen", exc_info=True)

    # --- Menü ---------------------------------------------------------------

    def _folder_items(self) -> list[MenuItem]:
        items: list[MenuItem] = []
        for worker in list(self.agent.workers):
            label = f"{worker.folder.source_label or worker.folder.local_path}: {worker.state}"
            if worker.folder.last_error:
                label = f"⚠ {worker.folder.source_label}: {worker.folder.last_error[:40]}"
            items.append(MenuItem(label, None, enabled=False))
        return items

    def _build_menu(self) -> pystray.Menu:
        status = MenuItem(self.agent.status_text(), None, enabled=False)
        return pystray.Menu(
            status,
            *self._folder_items(),
            pystray.Menu.SEPARATOR,
            MenuItem("Einstellungen …", lambda: self.on_settings(), default=True),
            MenuItem("Jetzt alles scannen", lambda: self.agent.scan_all_now()),
            MenuItem(
                "Pausiert",
                lambda: self._toggle_pause(),
                checked=lambda _i: self.agent.paused,
            ),
            pystray.Menu.SEPARATOR,
            MenuItem(f"Version {__version__}", None, enabled=False),
            MenuItem("Beenden", lambda: self.on_quit()),
        )

    def _toggle_pause(self) -> None:
        self.agent.set_paused(not self.agent.paused)

    # --- Lebenszyklus -------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._icon.run, daemon=True, name="tray")
        self._thread.start()
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop, daemon=True, name="tray-refresh"
        )
        self._refresh_thread.start()

    def stop(self) -> None:
        # Erst den Refresh-Thread stilllegen, dann das Symbol abräumen – sonst
        # griffe er womöglich noch auf ein gerade zerstörtes Icon zu.
        self._stopping.set()
        self._dirty.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=3)
        try:
            self._icon.stop()
        except Exception:
            log.debug("Tray konnte nicht sauber beendet werden", exc_info=True)
