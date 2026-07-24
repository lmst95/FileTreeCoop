"""Benachrichtigungs-Zähler (Navbar-Badges) und Aktivitäts-Feed.

Der Feed beantwortet die Kooperations-Kernfrage „Was ist passiert, seit ich
weg war?“: Annotationen anderer und abgeschlossene Scans, zeitlich gemischt.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.access import accessible_scopes, accessible_source_ids, scope_entry_condition
from app.auth import get_current_user
from app.db import get_db
from app.models import Annotation, Entry, Scan, Source, User, utcnow

router = APIRouter(prefix="/api", tags=["activity"])


@router.get("/notifications")
def notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Zähler für die Navbar-Badges – eine Abfrage pro Seitenaufruf.

    - ``handovers_open``  – an mich gerichtete Übergaben, noch nicht angenommen
    - ``handovers_active``– an mich gerichtete, unerledigte Übergaben gesamt
    - ``overdue``         – überfällige offene Aufgaben, die mich betreffen
      (an mich übergeben oder eigene Todos ohne Empfänger)
    - ``activity_new``    – fremde Aktivität seit meinem letzten Feed-Besuch
    """
    handovers_open = db.scalar(
        select(func.count(Annotation.id)).where(
            Annotation.type == "handover",
            Annotation.assignee_user_id == user.id,
            Annotation.status == "open",
            Annotation.done.is_(False),
        )
    )
    handovers_active = db.scalar(
        select(func.count(Annotation.id)).where(
            Annotation.type == "handover",
            Annotation.assignee_user_id == user.id,
            Annotation.done.is_(False),
        )
    )
    overdue = db.scalar(
        select(func.count(Annotation.id)).where(
            Annotation.done.is_(False),
            Annotation.due_date.is_not(None),
            Annotation.due_date < date.today(),
            or_(
                Annotation.assignee_user_id == user.id,
                and_(
                    Annotation.assignee_user_id.is_(None),
                    Annotation.author_user_id == user.id,
                ),
            ),
        )
    )

    activity_new = 0
    scopes = accessible_scopes(db, user)
    if scopes:
        seen = user.last_activity_seen_at
        ann_q = (
            select(func.count(Annotation.id))
            .join(Entry, Entry.id == Annotation.entry_id)
            .where(
                scope_entry_condition(scopes),
                Annotation.author_user_id != user.id,
            )
        )
        scan_q = select(func.count(Scan.id)).where(
            Scan.source_id.in_(accessible_source_ids(db, user)),
            Scan.status == "done",
            Scan.started_by_user_id != user.id,
        )
        if seen is not None:
            ann_q = ann_q.where(Annotation.created_at > seen)
            scan_q = scan_q.where(Scan.finished_at > seen)
        activity_new = (db.scalar(ann_q) or 0) + (db.scalar(scan_q) or 0)

    return {
        "handovers_open": handovers_open or 0,
        "handovers_active": handovers_active or 0,
        "overdue": overdue or 0,
        "activity_new": activity_new,
    }


@router.get("/activity")
def activity_feed(
    limit: int = Query(default=50, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Gemischter Feed: neueste Annotationen + Scans über alle Scopes."""
    scopes = accessible_scopes(db, user)
    if not scopes:
        return {"items": [], "seen_until": None}

    items: list[dict] = []

    rows = db.execute(
        select(Annotation, Entry, Source)
        .join(Entry, Entry.id == Annotation.entry_id)
        .join(Source, Source.id == Entry.source_id)
        .where(scope_entry_condition(scopes))
        .order_by(Annotation.created_at.desc())
        .limit(limit)
    ).all()
    author_ids = {a.author_user_id for a, _e, _s in rows}
    names = {
        u.id: u.display_name
        for u in db.scalars(select(User).where(User.id.in_(author_ids))).all()
    } if author_ids else {}
    for a, e, s in rows:
        items.append({
            "kind": "annotation",
            "when": a.created_at.isoformat(),
            "annotation_id": a.id,
            "type": a.type,
            "body": a.body,
            "label_value": a.label_value,
            "author_name": names.get(a.author_user_id, "?"),
            "is_own": a.author_user_id == user.id,
            "is_reply": a.parent_annotation_id is not None,
            "entry_id": e.id,
            "entry_name": e.name,
            "entry_path": e.path,
            "source_id": s.id,
            "source_label": s.label,
        })

    scan_rows = db.execute(
        select(Scan, Source)
        .join(Source, Source.id == Scan.source_id)
        .where(
            Scan.source_id.in_(accessible_source_ids(db, user)),
            Scan.status == "done",
        )
        .order_by(Scan.finished_at.desc())
        .limit(limit)
    ).all()
    scanner_ids = {sc.started_by_user_id for sc, _s in scan_rows if sc.started_by_user_id}
    scanner_names = {
        u.id: u.display_name
        for u in db.scalars(select(User).where(User.id.in_(scanner_ids))).all()
    } if scanner_ids else {}
    for sc, s in scan_rows:
        items.append({
            "kind": "scan",
            "when": (sc.finished_at or sc.started_at).isoformat(),
            "scan_id": sc.id,
            "by_name": scanner_names.get(sc.started_by_user_id),
            "is_own": sc.started_by_user_id == user.id,
            "initial": sc.initial,
            "added": sc.added,
            "changed": sc.changed,
            "missing": sc.missing,
            "moved": sc.moved,
            "reappeared": sc.reappeared,
            "source_id": s.id,
            "source_label": s.label,
        })

    items.sort(key=lambda i: i["when"], reverse=True)
    seen = user.last_activity_seen_at
    return {"items": items[:limit], "seen_until": seen.isoformat() if seen else None}


@router.post("/activity/seen")
def mark_activity_seen(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Feed als gesehen markieren – setzt das Aktivitäts-Badge zurück."""
    db_user = db.get(User, user.id)
    db_user.last_activity_seen_at = utcnow()
    db.commit()
    return {"ok": True}
