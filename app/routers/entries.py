"""Einträge einer Quelle browsen – scope-genau (ganze Quelle oder Teilbaum)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import (
    accessible_scopes,
    like_escape,
    path_in_scope,
    scope_entry_condition,
    scopes_for_source,
    shallowest_prefixes,
)
from app.auth import get_current_user
from app.db import get_db
from app.models import Annotation, Entry, Source, SourceVisit, User
from app.routers.sources import _accessible_source
from app.schemas import SearchHit
from app.serializers import annotations_out, has_new_annotations

router = APIRouter(prefix="/api/sources", tags=["entries"])


def _to_hits(entries, source: Source, db: Session, user: User) -> list[SearchHit]:
    """Baut SearchHit-Objekte inkl. zugehöriger Annotationen (in einer Query)."""
    ids = [e.id for e in entries]
    anns_by_entry: dict[int, list[Annotation]] = {}
    if ids:
        for a in db.scalars(
            select(Annotation).where(Annotation.entry_id.in_(ids))
        ).all():
            anns_by_entry.setdefault(a.entry_id, []).append(a)
    # Letzter Besuch dieser Quelle -> Ungelesen-Punkt für neuere fremde Notizen.
    visit = db.scalar(
        select(SourceVisit.last_seen_at).where(
            SourceVisit.user_id == user.id, SourceVisit.source_id == source.id
        )
    )
    return [
        SearchHit(
            entry_id=e.id,
            source_id=e.source_id,
            source_label=source.label,
            path=e.path,
            name=e.name,
            ext=e.ext,
            is_dir=e.is_dir,
            status=e.status,
            annotations=annotations_out(db, anns_by_entry.get(e.id, [])),
            has_new=has_new_annotations(anns_by_entry.get(e.id, []), user.id, visit),
        )
        for e in entries
    ]


@router.get("/{source_id}/entries", response_model=list[SearchHit])
def list_entries(
    source_id: int,
    prefix: str = Query(default="", description="Pfad-Präfix zum Filtern"),
    limit: int = Query(default=200, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _accessible_source(db, user, source_id)
    scopes = scopes_for_source(accessible_scopes(db, user), source.id)
    stmt = select(Entry).where(
        Entry.source_id == source.id, scope_entry_condition(scopes)
    )
    if prefix:
        stmt = stmt.where(Entry.path.like(f"{like_escape(prefix)}%", escape="\\"))
    stmt = stmt.order_by(Entry.is_dir.desc(), Entry.path).limit(limit)
    entries = db.scalars(stmt).all()
    return _to_hits(entries, source, db, user)


@router.get("/{source_id}/children", response_model=list[SearchHit])
def list_children(
    source_id: int,
    parent: str = Query(default="", description="Ordnerpfad; leer = Wurzel"),
    limit: int = Query(default=1000, le=5000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Direkte Kinder eines Ordners (Baumansicht, Lazy-Loading), scope-genau.

    An der Wurzel (parent="") sieht ein Voll-Zugriff die oberste Ebene; wer nur
    Teilbäume freigegeben bekam, sieht diese freigegebenen Ordner als Wurzeln.
    """
    source = _accessible_source(db, user, source_id)
    scopes = scopes_for_source(accessible_scopes(db, user), source.id)
    has_full = any(s.path_prefix == "" for s in scopes)

    stmt = select(Entry).where(Entry.source_id == source.id)

    if not parent:
        if has_full:
            stmt = stmt.where(func.instr(Entry.path, "/") == 0)
        else:
            # Wurzeln = die freigegebenen Teilbaum-Ordner selbst.
            roots = shallowest_prefixes([s.path_prefix for s in scopes if s.path_prefix])
            if not roots:
                return []
            stmt = stmt.where(Entry.path.in_(roots))
    else:
        # Nur browsen, wenn der Ordner selbst im Zugriff liegt.
        if not any(path_in_scope(parent, s.path_prefix) for s in scopes):
            raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Ordner")
        like = f"{like_escape(parent)}/%"
        remainder = func.substr(Entry.path, len(parent) + 2)
        stmt = stmt.where(
            Entry.path.like(like, escape="\\"), func.instr(remainder, "/") == 0
        )

    stmt = stmt.order_by(Entry.is_dir.desc(), Entry.name).limit(limit)
    entries = db.scalars(stmt).all()
    return _to_hits(entries, source, db, user)
