"""Suche über den FTS5-Index, gefiltert auf zugängliche Quellen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import accessible_scopes
from app.auth import get_current_user
from app.db import get_db
from app.models import Annotation, Entry, Source, SourceVisit, User
from app.schemas import SearchHit
from app.search import build_match_query, search_entry_ids
from app.serializers import annotations_out, has_new_annotations

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=list[SearchHit])
def search(
    q: str = Query(default="", description="Beschreibender Suchstring"),
    source_id: int | None = None,
    status: str | None = Query(default=None, description="present|missing"),
    limit: int = Query(default=100, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match_query = build_match_query(q)
    scopes = accessible_scopes(db, user)
    entry_ids = search_entry_ids(
        db,
        match_query,
        scopes,
        source_id=source_id,
        status=status,
        limit=limit,
    )
    if not entry_ids:
        return []

    # Einträge + Quelle + Annotationen laden; Original-Ranking-Reihenfolge wahren.
    entries = {
        e.id: e
        for e in db.scalars(select(Entry).where(Entry.id.in_(entry_ids))).all()
    }
    sources = {
        s.id: s
        for s in db.scalars(
            select(Source).where(Source.id.in_({e.source_id for e in entries.values()}))
        ).all()
    }
    anns_by_entry: dict[int, list[Annotation]] = {}
    for a in db.scalars(
        select(Annotation).where(Annotation.entry_id.in_(entry_ids))
    ).all():
        anns_by_entry.setdefault(a.entry_id, []).append(a)

    # Letzte Besuche je Quelle -> Ungelesen-Punkte auch in der Suche.
    visits = {
        v.source_id: v.last_seen_at
        for v in db.scalars(
            select(SourceVisit).where(SourceVisit.user_id == user.id)
        ).all()
    }

    hits: list[SearchHit] = []
    for eid in entry_ids:
        e = entries.get(eid)
        if e is None:
            continue
        s = sources.get(e.source_id)
        hits.append(
            SearchHit(
                entry_id=e.id,
                source_id=e.source_id,
                source_label=s.label if s else "?",
                path=e.path,
                name=e.name,
                ext=e.ext,
                is_dir=e.is_dir,
                status=e.status,
                annotations=annotations_out(db, anns_by_entry.get(eid, [])),
                has_new=has_new_annotations(
                    anns_by_entry.get(eid, []), user.id, visits.get(e.source_id)
                ),
            )
        )
    return hits
