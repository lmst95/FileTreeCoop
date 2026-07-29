"""Voll-Scan eines Ordners: rekursiv laufen und Metadaten in Batches melden.

Das Gegenstück zum Browser-Scanner (``app/static/js/scanner.js``) und bewusst
mit demselben Verhalten – insbesondere bei Fehlern:

- Ist die **Wurzel** nicht lesbar (Netzlaufwerk nicht verbunden), bricht der
  Scan ab, *bevor* eine Abschluss-Batch gesendet wird. Der Index bleibt
  unangetastet, statt dass die ganze Quelle als „verschwunden“ gilt.
- Ist ein **Unterordner** nicht lesbar, wird nur dieser übersprungen und
  gemeldet; der Lauf verzichtet dann auf die „verschwunden“-Erkennung, damit
  kurz unerreichbare Ordner nicht fälschlich als gelöscht gelten.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .api import Api

BATCH_SIZE = 500


class SourceUnreachable(RuntimeError):
    """Die Wurzel selbst ist nicht lesbar – der Scan wird nicht durchgeführt."""


@dataclass
class ScanResult:
    total: int = 0
    added: int = 0
    changed: int = 0
    moved: int = 0
    missing: int = 0
    reappeared: int = 0
    skipped: int = 0
    cancelled: bool = False


def ext_of(name: str) -> str:
    i = name.rfind(".")
    return name[i + 1:].lower() if i > 0 else ""


def walk(root: Path, on_skip=None):
    """Rekursiv durch den Baum; liefert Einträge relativ zur Wurzel.

    Bewusst iterativ mit eigenem Stapel statt ``os.walk``: so lässt sich pro
    Verzeichnis einzeln entscheiden, was bei einem Fehler passiert, ohne den
    ganzen Lauf zu verlieren.
    """
    try:
        os.scandir(root).close()
    except OSError as e:
        raise SourceUnreachable(str(e)) from e

    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        current, prefix = stack.pop()
        try:
            with os.scandir(current) as it:
                items = list(it)
        except OSError as e:
            if on_skip:
                on_skip(prefix, type(e).__name__)
            continue
        for item in items:
            path = f"{prefix}/{item.name}" if prefix else item.name
            try:
                is_dir = item.is_dir(follow_symlinks=False)
            except OSError:
                # Zwischen Auflisten und Zugriff verschwunden – überspringen.
                continue
            if is_dir:
                yield {
                    "path": path, "name": item.name, "is_dir": True,
                    "size": 0, "mtime": 0.0, "ext": "",
                }
                stack.append((Path(item.path), path))
                continue
            size = 0.0
            mtime = 0.0
            try:
                st = item.stat(follow_symlinks=False)
                size = st.st_size
                mtime = st.st_mtime
            except OSError:
                # Datei trotzdem erfassen – „da, aber nicht lesbar“ ist eine
                # Information, kein Grund, sie aus dem Index zu lassen.
                pass
            yield {
                "path": path, "name": item.name, "is_dir": False,
                "size": int(size), "mtime": float(mtime), "ext": ext_of(item.name),
            }


def full_scan(api: Api, source_id: int, root: Path, *, cancel=None, on_progress=None) -> ScanResult:
    """Kompletten Baum erfassen und mit dem Server abgleichen.

    ``cancel`` ist ein ``threading.Event``; wird es gesetzt, bricht der Lauf ab,
    **ohne** zu finalisieren – ein halber Scan darf den Index nicht aufräumen.
    """
    scan_id = str(uuid.uuid4())
    result = ScanResult()
    skipped: list[dict] = []
    buffer: list[dict] = []

    def on_skip(path: str, reason: str) -> None:
        skipped.append({"path": path, "reason": reason})

    def flush(finalize: bool) -> dict | None:
        if not buffer and not finalize:
            return None
        res = api.ingest(
            source_id,
            buffer,
            scan_id=scan_id,
            finalize=finalize,
            kind="full",
            skipped=skipped if finalize else None,
            # Unvollständiger Lauf: „verschwunden“-Erkennung aussetzen, sonst
            # gälte der Inhalt übersprungener Ordner als gelöscht.
            mark_missing=not skipped,
        )
        buffer.clear()
        return res

    for entry in walk(root, on_skip):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            return result
        buffer.append(entry)
        result.total += 1
        if len(buffer) >= BATCH_SIZE:
            flush(False)
            if on_progress:
                on_progress(result.total)

    final = flush(True) or {}
    result.added = final.get("added", 0)
    result.changed = final.get("changed", 0)
    result.moved = final.get("moved", 0)
    result.missing = final.get("marked_missing", 0)
    result.reappeared = final.get("reappeared", 0)
    result.skipped = len(skipped)
    if on_progress:
        on_progress(result.total)
    return result


def stat_entry(root: Path, rel_path: str) -> dict | None:
    """Einen einzelnen Pfad für ein Live-Delta erfassen (None = existiert nicht)."""
    full = root / rel_path
    try:
        st = full.stat()
    except OSError:
        return None
    is_dir = full.is_dir()
    name = full.name
    return {
        "path": rel_path.replace("\\", "/"),
        "name": name,
        "is_dir": is_dir,
        "size": 0 if is_dir else int(st.st_size),
        "mtime": 0.0 if is_dir else float(st.st_mtime),
        "ext": "" if is_dir else ext_of(name),
    }
