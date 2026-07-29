"""Voll-Backup: Download der kompletten SQLite-Datenbank.

Anders als die Exporte in :mod:`app.routers.export` ist das kein
scope-gefilterter Auszug, sondern die ganze Datei – mit *allen* Nutzern,
Passwort-Hashes und gespeicherten LLM-Tokens. Deshalb kommt nur ein
Betreiber-Konto daran (siehe :func:`is_backup_admin`).

Die Kopie entsteht über die Online-Backup-API von SQLite und ist damit auch
dann in sich konsistent, wenn währenddessen geschrieben wird — ein simples
Kopieren der Datei wäre das nicht.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.auth import get_session_user
from app.config import settings
from app.db import engine, get_db
from app.models import User, utcnow

router = APIRouter(prefix="/api/admin", tags=["backup"])


def is_backup_admin(db: Session, user: User) -> bool:
    """Darf ``user`` die ganze Datenbank herunterladen?

    Konfigurierbar über ``FTC_BACKUP_ADMINS`` (Usernames und/oder E-Mails,
    kommagetrennt). Ohne Konfiguration gilt das zuerst registrierte Konto als
    Betreiber – bei einer selbst aufgesetzten Instanz ist das der, der sie
    aufgesetzt hat.
    """
    allowed = settings.backup_admins
    if allowed:
        return (
            (user.username or "").lower() in allowed
            or (user.email or "").lower() in allowed
        )
    first_id = db.scalar(select(func.min(User.id)))
    return first_id is not None and user.id == first_id


def database_size_bytes() -> int:
    """Größe der DB-Datei (0, falls sie noch nicht existiert)."""
    try:
        return Path(settings.db_path).stat().st_size
    except OSError:
        return 0


def database_size_human() -> str:
    """DB-Größe als kurzer Text mit deutschem Dezimalkomma (z. B. "2,4 MB")."""
    size = database_size_bytes() / 1024
    unit = "KB"
    for nxt in ("MB", "GB"):
        if size < 1024:
            break
        size /= 1024
        unit = nxt
    return f"{size:.1f} {unit}".replace(".", ",")


def _snapshot(target: Path) -> None:
    """Schreibt eine konsistente Kopie der laufenden DB nach ``target``."""
    raw = engine.raw_connection()
    try:
        dest = sqlite3.connect(target)
        try:
            raw.driver_connection.backup(dest)
        finally:
            dest.close()
    finally:
        raw.close()


@router.get("/backup.db")
def download_backup(
    # Bewusst ``get_session_user``: ein Gerätetoken des Desktop-Clients darf die
    # Datei mit allen Passwort-Hashes und LLM-Tokens nicht herausgeben können.
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    """Komplette Datenbank als SQLite-Datei zum Herunterladen."""
    if not is_backup_admin(db, user):
        raise HTTPException(
            status_code=403,
            detail="Nur der Betreiber darf ein Voll-Backup herunterladen",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="ftc-backup-"))
    try:
        target = tmp_dir / "backup.sqlite3"
        _snapshot(target)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    filename = f"filetree_coop-backup-{utcnow().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    return FileResponse(
        target,
        media_type="application/x-sqlite3",
        filename=filename,
        # Temp-Datei erst wegräumen, wenn die Antwort draußen ist.
        background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
    )
