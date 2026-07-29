"""Live-Überwachung eines Ordners mit ``watchdog``.

Der Handler tut absichtlich fast nichts: er merkt sich je betroffenem Pfad,
*wann* er zuletzt gewackelt hat. Ausgewertet (Stat holen, an den Server melden)
wird zeitversetzt, und zwar **pro Pfad einzeln**:

Ein Pfad gilt erst als sync-reif, wenn er ``settle_seconds`` lang Ruhe gegeben
hat. Das ist wichtiger, als es klingt – ohne diese Karenz landete lauter Unsinn
im Index:

- Dateien, die noch **geschrieben** werden (Kopieren, Export, Download), kämen
  mit halber Größe an und müssten sofort wieder korrigiert werden.
- **Temporärdateien** vieler Programme (``.~lock``, ``Dokument.tmp``, Office- und
  Editor-Zwischenstände) entstehen und verschwinden im Sekundenbereich; sie
  würden angelegt und im nächsten Atemzug als „verschwunden“ markiert.
- Ein **Umbenennen** erzeugt zwei Ereignisse (alter Pfad weg, neuer da). Erst
  nach der Karenz sind beide da und ergeben zusammen ein sauberes Bild.

Der Zähler wird bei jedem neuen Ereignis für diesen Pfad zurückgesetzt: eine
Datei, in die fortlaufend geschrieben wird, wird also erst gemeldet, wenn sie
wirklich fertig ist – währenddessen bleiben alle anderen Pfade unbehelligt.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Voreinstellung, wie lange ein Pfad Ruhe geben muss. Großzügig genug für einen
# Speichervorgang über ein Netzlaufwerk, kurz genug, dass sich „live“ auch live
# anfühlt. Je Ordner einstellbar.
DEFAULT_SETTLE_SECONDS = 10


class ChangeCollector(FileSystemEventHandler):
    """Sammelt geänderte Pfade (relativ zur Wurzel) samt Zeitpunkt."""

    def __init__(self, root: Path, settle_seconds: float = DEFAULT_SETTLE_SECONDS):
        self.root = root
        self.settle_seconds = max(0.0, float(settle_seconds))
        self._lock = threading.Lock()
        # rel-Pfad -> Zeitpunkt des letzten Ereignisses (monotone Uhr).
        self._seen: dict[str, float] = {}
        # Signalisiert dem Worker, dass überhaupt etwas anliegt.
        self.event = threading.Event()

    # --- Ereignisse ---------------------------------------------------------

    def _touch(self, raw_path) -> None:
        if raw_path is None:
            return
        try:
            rel = Path(os.fsdecode(raw_path)).relative_to(self.root)
        except (ValueError, OSError):
            return  # außerhalb der Wurzel – geht uns nichts an
        rel_str = rel.as_posix()
        if not rel_str or rel_str == ".":
            return
        now = time.monotonic()
        with self._lock:
            self._seen[rel_str] = now
        self.event.set()

    def on_any_event(self, event) -> None:
        # ``moved`` betrifft beide Seiten: der alte Pfad ist weg, der neue da.
        self._touch(getattr(event, "src_path", None))
        self._touch(getattr(event, "dest_path", None))

    # --- Abholen ------------------------------------------------------------

    def take_settled(self) -> set[str]:
        """Pfade, die lange genug Ruhe gegeben haben – und nur die.

        Unruhige Pfade bleiben liegen und werden beim nächsten Aufruf erneut
        geprüft. So blockiert eine Datei, in die ununterbrochen geschrieben wird,
        nicht die Meldung aller anderen.
        """
        now = time.monotonic()
        with self._lock:
            ready = {
                path
                for path, last in self._seen.items()
                if now - last >= self.settle_seconds
            }
            for path in ready:
                del self._seen[path]
            if not self._seen:
                self.event.clear()
        return ready

    def seconds_until_next(self) -> float | None:
        """Wie lange bis der nächste Pfad reif wird (None = nichts anliegend)."""
        with self._lock:
            if not self._seen:
                return None
            oldest = min(self._seen.values())
        return max(0.0, self.settle_seconds - (time.monotonic() - oldest))

    def pending_count(self) -> int:
        with self._lock:
            return len(self._seen)

    def requeue(self, paths) -> None:
        """Pfade zurücklegen – ihre Karenz beginnt von vorn.

        Gebraucht für Dateien, deren Änderungszeit beim Abholen noch taufrisch
        war: manche Programme schreiben in großen Blöcken, ohne dass für jeden
        ein Ereignis ankommt. Ein Blick auf die mtime fängt das ab.
        """
        now = time.monotonic()
        with self._lock:
            for path in paths:
                self._seen[path] = now
        if paths:
            self.event.set()


def start_observer(root: Path, collector: ChangeCollector) -> Observer:
    observer = Observer()
    observer.schedule(collector, str(root), recursive=True)
    observer.start()
    return observer
