"""Regressionstests für das Taskleisten-Symbol.

Hintergrund: ``pystray._win32`` gibt beim Symbolwechsel erst das alte
Icon-Handle frei und erzeugt dann ein neues. Zwei Threads gleichzeitig darin =
``OSError: [WinError 1402] Ungültiges Cursorhandle``. Genau das passierte, weil
jeder Sync-Worker und der Heartbeat ``refresh()`` direkt aufriefen.

Ausgeführt aus ``client/``:  ``pytest``
"""

from __future__ import annotations

import threading
import time

import pytest

from ftc_client.config import Config, FolderConfig
from ftc_client.tray import Tray


class FakeFolder(FolderConfig):
    pass


class FakeWorker:
    def __init__(self, label: str):
        self.folder = FolderConfig(source_id=1, source_label=label, local_path="C:\\x")
        self.state = "aktuell"

    def is_alive(self) -> bool:
        return True


class FakeAgent:
    """Nur so viel Agent, wie das Tray anfasst."""

    def __init__(self):
        self.config = Config(server_url="http://x", token="t", client_name="Test")
        self.workers = [FakeWorker("Quelle A")]
        self.paused = False
        self.online = True

    def status_text(self) -> str:
        return f"{len(self.workers)} Ordner aktuell"

    def scan_all_now(self) -> None:
        pass

    def set_paused(self, value: bool) -> None:
        self.paused = value


class SpyIcon:
    """Ersetzt pystray.Icon und meldet gleichzeitige Zugriffe.

    Genau die Gleichzeitigkeit war der Fehler – ein echter ``DestroyIcon``-Aufruf
    ist dafür nicht nötig, nur der Nachweis, dass zwei Threads zugleich drin sind.
    """

    def __init__(self):
        self._icon = None
        self.title = ""
        self.menu = None
        self.icon_writes = 0
        self.menu_writes = 0
        self.concurrent = False
        self._inside = 0
        self._guard = threading.Lock()

    def _enter(self) -> None:
        with self._guard:
            self._inside += 1
            if self._inside > 1:
                self.concurrent = True
        time.sleep(0.002)  # Fenster für einen echten Konflikt aufreißen
        with self._guard:
            self._inside -= 1

    def __setattr__(self, name, value):
        if name == "icon" and hasattr(self, "_guard"):
            self._enter()
            object.__setattr__(self, "icon_writes", self.icon_writes + 1)
        object.__setattr__(self, name, value)

    def update_menu(self) -> None:
        self._enter()
        object.__setattr__(self, "menu_writes", self.menu_writes + 1)

    def run(self) -> None:
        while True:
            time.sleep(0.05)

    def stop(self) -> None:
        pass


@pytest.fixture
def tray(monkeypatch):
    agent = FakeAgent()
    t = Tray(agent, lambda: None, lambda: None)
    # Erst nach dem Bau ersetzen: der Konstruktor braucht ein echtes Icon-Objekt.
    t._icon = SpyIcon()
    yield t, agent, t._icon
    t._stopping.set()


def test_refresh_does_not_touch_the_icon_directly(tray):
    """``refresh()`` darf aus jedem Thread kommen – und fasst deshalb nichts an."""
    t, _agent, spy = tray
    for _ in range(50):
        t.refresh()
    assert spy.icon_writes == 0
    assert spy.menu_writes == 0
    assert t._dirty.is_set()


def test_many_threads_refreshing_never_collide(tray):
    """Der Fall aus dem Fehlerbericht: viele Worker melden gleichzeitig."""
    t, agent, spy = tray
    t._refresh_thread = threading.Thread(target=t._refresh_loop, daemon=True)
    t._refresh_thread.start()

    stop = threading.Event()

    def spam(n: int) -> None:
        i = 0
        while not stop.is_set():
            # Zustand wirklich verändern, sonst gäbe es nichts zu tun.
            agent.workers[0].state = f"Hashes … {n}-{i}"
            t.refresh()
            i += 1
            time.sleep(0.001)

    threads = [threading.Thread(target=spam, args=(n,), daemon=True) for n in range(8)]
    for th in threads:
        th.start()
    time.sleep(2.0)
    stop.set()
    for th in threads:
        th.join(timeout=2)
    t._stopping.set()
    t._dirty.set()
    t._refresh_thread.join(timeout=3)

    assert not spy.concurrent, "Zwei Threads waren gleichzeitig im Icon – WinError 1402"
    assert spy.menu_writes > 0, "gar nichts aktualisiert – der Loop lief nicht"


def test_icon_is_only_rewritten_when_the_colour_changes(tray):
    """Der teure Aufruf (Handle freigeben + neu) darf nicht im 400-ms-Takt laufen."""
    t, agent, spy = tray
    t._apply()
    baseline = spy.icon_writes

    # Fortschrittsmeldungen: Text ändert sich, Zustand bleibt „busy“.
    for i in range(20):
        agent.workers[0].state = f"Hashes … {i}"
        t._apply()
    after_busy = spy.icon_writes
    assert after_busy == baseline + 1, "Farbwechsel nach busy genau einmal"

    # Erst ein echter Zustandswechsel fasst das Symbol wieder an.
    agent.workers[0].state = "aktuell"
    t._apply()
    assert spy.icon_writes == after_busy + 1


def test_state_reflects_agent(tray):
    t, agent, _spy = tray
    assert t._state() == "ok"
    agent.workers[0].state = "Voll-Scan läuft …"
    assert t._state() == "busy"
    agent.workers[0].folder.last_error = "Ordner nicht gefunden"
    assert t._state() == "error"
    agent.paused = True
    assert t._state() == "paused"
    agent.paused = False
    agent.online = False
    assert t._state() == "offline"


def test_state_survives_workers_being_swapped(tray):
    """Beim Neuladen der Konfiguration tauscht ein anderer Thread die Liste aus."""
    t, agent, _spy = tray
    stop = threading.Event()

    def churn() -> None:
        while not stop.is_set():
            agent.workers = [FakeWorker(f"Q{i}") for i in range(5)]
            agent.workers = []

    th = threading.Thread(target=churn, daemon=True)
    th.start()
    for _ in range(2000):
        t._state()
        t._menu_signature()
    stop.set()
    th.join(timeout=2)
