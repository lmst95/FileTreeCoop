"""Inhalts-Hashes (SHA-256) im Hintergrund berechnen.

Derselbe Nachlauf, den sonst der Browser fährt: Der Server nennt die Dateien
ohne gültigen Hash, hier wird gelesen und gerechnet, zurück geht nur der
Hex-String – der Inhalt verlässt den Rechner nie.

Ein Vorteil gegenüber dem Browser: hier wird **strömend** gehasht (Block für
Block), statt die Datei komplett in den Speicher zu laden. Die 256-MB-Grenze
der Browser-Variante entfällt damit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .api import Api

# Arbeitsliste in großen Blöcken holen, Ergebnisse in kleinen zurückschicken –
# so wartet der Client nicht ständig auf den Server (vgl. scanner.js).
TODO_BATCH = 5000
POST_BATCH = 100
CHUNK = 1024 * 1024


@dataclass
class HashResult:
    hashed: int = 0
    errors: int = 0
    reconciled: int = 0
    cancelled: bool = False
    # Einträge, die trotz Bearbeitung erneut in der Arbeitsliste auftauchten.
    stuck: int = 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def hash_pending(
    api: Api, source_id: int, root: Path, *, cancel=None, on_progress=None
) -> HashResult:
    """Alles nachrechnen, was noch keinen gültigen Hash hat.

    Jederzeit abbrechbar – der nächste Lauf macht dort weiter, wo dieser
    aufgehört hat, weil der Server die Arbeitsliste führt.
    """
    result = HashResult()
    pending: list[dict] = []
    # Was in diesem Lauf schon bearbeitet wurde. Taucht ein Pfad erneut in der
    # Arbeitsliste auf, hat der Server ihn nicht als erledigt verbucht – dann
    # wird abgebrochen, statt endlos im Kreis zu laufen.
    handled: set[str] = set()

    def flush() -> None:
        if not pending:
            return
        res = api.submit_hashes(source_id, pending) or {}
        result.reconciled += res.get("reconciled", 0)
        pending.clear()

    while True:
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break
        todo = api.hash_todo(source_id, TODO_BATCH)
        if not todo:
            break
        fresh = [item for item in todo if item["path"] not in handled]
        if not fresh:
            result.stuck = len(todo)
            break

        for item in fresh:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                break
            handled.add(item["path"])
            full = root / item["path"]
            record = {"path": item["path"], "size": item["size"], "mtime": item["mtime"]}
            try:
                st = full.stat()
                record["size"] = int(st.st_size)
                record["mtime"] = float(st.st_mtime)
                record["sha256"] = sha256_file(full)
                record["state"] = "ok"
                result.hashed += 1
            except OSError:
                record["state"] = "error"
                result.errors += 1
            pending.append(record)
            if len(pending) >= POST_BATCH:
                flush()
                if on_progress:
                    on_progress(result)
        flush()
        if on_progress:
            on_progress(result)
        if result.cancelled:
            break

    flush()
    return result
