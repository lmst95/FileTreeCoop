"""Exporte: iCal-Kalender (.ics), Annotationen-CSV, JSON je Quelle.

Grundgedanke: die Daten sind nicht im Werkzeug gefangen. Der iCal-Export
bringt Termine in Outlook/Apple/Google Calendar (inkl. Erinnerungen dort),
CSV/JSON dienen als Backup und Weiterverarbeitung.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import accessible_scopes, scope_entry_condition, scopes_for_source
from app.auth import get_current_user
from app.db import get_db
from app.models import Annotation, Entry, Source, User, utcnow
from app.routers.sources import _accessible_source

router = APIRouter(prefix="/api", tags=["export"])

_TYPE_LABEL = {"note": "Notiz", "todo": "Todo", "label": "Label", "handover": "Übergabe"}
_TYPE_ICON = {"todo": "☑", "handover": "➦", "note": "📝", "label": "🏷"}


def _ics_escape(text: str) -> str:
    """RFC-5545-Escaping für Textwerte."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _user_names(db: Session, annotations: list[Annotation]) -> dict[int, str]:
    ids = {a.author_user_id for a in annotations}
    ids |= {a.assignee_user_id for a in annotations if a.assignee_user_id}
    if not ids:
        return {}
    return {
        u.id: u.display_name
        for u in db.scalars(select(User).where(User.id.in_(ids))).all()
    }


@router.get("/export/calendar.ics")
def export_calendar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle offenen Aufgaben mit Termin als Ganztages-Events."""
    scopes = accessible_scopes(db, user)
    rows = []
    if scopes:
        rows = db.execute(
            select(Annotation, Entry, Source)
            .join(Entry, Entry.id == Annotation.entry_id)
            .join(Source, Source.id == Entry.source_id)
            .where(
                scope_entry_condition(scopes),
                Annotation.due_date.is_not(None),
                Annotation.done.is_(False),
            )
            .order_by(Annotation.due_date)
        ).all()

    names = _user_names(db, [a for a, _e, _s in rows])
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//filetree_coop//DE",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:filetree_coop – Aufgaben ({_ics_escape(user.display_name)})",
    ]
    for a, e, s in rows:
        icon = _TYPE_ICON.get(a.type, "")
        title_parts = [f"{icon} {e.name}".strip()]
        if a.body:
            title_parts.append(a.body)
        if a.type == "handover" and a.assignee_user_id:
            title_parts.append(f"→ {names.get(a.assignee_user_id, '?')}")
        desc = (
            f"{_TYPE_LABEL.get(a.type, a.type)} von {names.get(a.author_user_id, '?')}"
            f" · Quelle: {s.label} · Pfad: {e.path}"
        )
        day: date = a.due_date
        lines += [
            "BEGIN:VEVENT",
            f"UID:ftc-ann-{a.id}@filetree-coop",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_ics_escape(' — '.join(title_parts))}",
            f"DESCRIPTION:{_ics_escape(desc)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines) + "\r\n",
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="filetree_coop.ics"'},
    )


@router.get("/export/annotations.csv")
def export_annotations_csv(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle zugänglichen Annotationen als CSV (Backup/Weiterverarbeitung)."""
    scopes = accessible_scopes(db, user)
    rows = []
    if scopes:
        rows = db.execute(
            select(Annotation, Entry, Source)
            .join(Entry, Entry.id == Annotation.entry_id)
            .join(Source, Source.id == Entry.source_id)
            .where(scope_entry_condition(scopes))
            .order_by(Source.label, Entry.path, Annotation.created_at)
        ).all()
    names = _user_names(db, [a for a, _e, _s in rows])

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")  # Semikolon: deutschsprachiges Excel
    w.writerow([
        "quelle", "pfad", "datei", "typ", "text", "label", "autor",
        "empfaenger", "status", "erledigt", "faellig", "erstellt",
    ])
    for a, e, s in rows:
        w.writerow([
            s.label, e.path, e.name, _TYPE_LABEL.get(a.type, a.type),
            a.body, a.label_value, names.get(a.author_user_id, ""),
            names.get(a.assignee_user_id, "") if a.assignee_user_id else "",
            a.status if a.type == "handover" else "",
            "ja" if a.done else "nein",
            a.due_date.isoformat() if a.due_date else "",
            a.created_at.date().isoformat(),
        ])
    # BOM, damit Excel das UTF-8 erkennt.
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="filetree_coop_annotationen.csv"'
        },
    )


@router.get("/sources/{source_id}/export.json")
def export_source_json(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Kompletter (scope-gefilterter) Export einer Quelle inkl. Annotationen."""
    source = _accessible_source(db, user, source_id)
    scopes = scopes_for_source(accessible_scopes(db, user), source.id)
    if not scopes:
        raise HTTPException(status_code=403, detail="Kein Zugriff")

    entries = db.scalars(
        select(Entry)
        .where(Entry.source_id == source.id, scope_entry_condition(scopes))
        .order_by(Entry.path)
    ).all()
    anns_by_entry: dict[int, list[Annotation]] = {}
    all_anns: list[Annotation] = []
    if entries:
        for a in db.scalars(
            select(Annotation).where(
                Annotation.entry_id.in_([e.id for e in entries])
            )
        ).all():
            anns_by_entry.setdefault(a.entry_id, []).append(a)
            all_anns.append(a)
    names = _user_names(db, all_anns)

    def ann_dict(a: Annotation) -> dict:
        return {
            "type": a.type,
            "body": a.body,
            "label": a.label_value,
            "author": names.get(a.author_user_id),
            "assignee": names.get(a.assignee_user_id) if a.assignee_user_id else None,
            "done": a.done,
            "status": a.status if a.type == "handover" else None,
            "due_date": a.due_date.isoformat() if a.due_date else None,
            "created_at": a.created_at.isoformat(),
            "parent_annotation_id": a.parent_annotation_id,
            "id": a.id,
        }

    return {
        "exported_at": utcnow().isoformat(),
        "source": {
            "id": source.id,
            "label": source.label,
            "kind": source.kind,
            "host_hint": source.host_hint,
            "last_scanned_at": (
                source.last_scanned_at.isoformat() if source.last_scanned_at else None
            ),
        },
        "entries": [
            {
                "path": e.path,
                "name": e.name,
                "is_dir": e.is_dir,
                "size": e.size,
                "mtime": e.mtime,
                "status": e.status,
                "annotations": [ann_dict(a) for a in anns_by_entry.get(e.id, [])],
            }
            for e in entries
        ],
    }
