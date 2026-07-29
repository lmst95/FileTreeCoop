"""Suche über den FTS5-Index, gefiltert auf zugängliche Quellen.

Neben der klassischen Freitextsuche liegt hier der **Suchassistent**: er lässt
ein LLM die Frage in genau die Filter übersetzen, die die Suche ohnehin kennt,
und führt danach die ganz normale Suche aus.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import search_assist
from app.access import accessible_scopes
from app.auth import get_current_user
from app.db import get_db
from app.ignores import active_rules
from app.llm import service
from app.llm.jsonutil import json_obj
from app.llm.providers import LLMError
from app.models import (
    Annotation,
    Entry,
    LLMConnection,
    LLMPrompt,
    LLMSetting,
    Source,
    SourceVisit,
    User,
)
from app.schemas import SearchAssistIn, SearchAssistOut, SearchFiltersOut, SearchHit
from app.search import (
    SEARCH_MODES,
    SearchFilters,
    SearchQueryError,
    build_query,
    clean_fields,
    search_entry_ids,
)
from app.serializers import annotations_out, has_new_annotations

router = APIRouter(prefix="/api/search", tags=["search"])


def _hits_for(db: Session, user: User, entry_ids: list[int]) -> list[SearchHit]:
    """Baut die Trefferliste zu einer Menge von Entry-IDs (Reihenfolge bleibt)."""
    if not entry_ids:
        return []

    entries = {
        e.id: e for e in db.scalars(select(Entry).where(Entry.id.in_(entry_ids))).all()
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


def _ext_list(raw: str | None) -> list[str]:
    """„pdf, .docx“ -> ["pdf", "docx"]"""
    if not raw:
        return []
    return [part.strip().lstrip(".").lower() for part in raw.split(",") if part.strip()]


def _field_list(raw: str | None) -> list[str]:
    """„name,path“ -> ["name", "path"]; Unbekanntes fällt weg (= überall suchen)."""
    if not raw:
        return []
    return clean_fields([part.strip().lower() for part in raw.split(",")])


@router.get("", response_model=list[SearchHit])
def search(
    q: str = Query(default="", description="Beschreibender Suchstring"),
    mode: str = Query(
        default="smart",
        description="smart (Volltext) | exact (Teilstring) | glob (*.pdf) | regex",
    ),
    source_id: int | None = None,
    status: str | None = Query(default=None, description="present|missing"),
    fields: str | None = Query(
        default=None,
        description="Suchbereich: name|path|notes, kommagetrennt; leer = überall",
    ),
    ext: str | None = Query(default=None, description="Endungen, kommagetrennt"),
    modified_after: date | None = Query(default=None, description="ab Änderungsdatum"),
    modified_before: date | None = Query(default=None, description="bis Änderungsdatum"),
    min_size: int | None = Query(default=None, ge=0, description="Mindestgröße in Bytes"),
    max_size: int | None = Query(default=None, ge=0, description="Maximalgröße in Bytes"),
    is_dir: bool | None = Query(default=None),
    apply_ignores: bool = Query(
        default=True, description="Gespeicherte Ignorierregeln anwenden"
    ),
    limit: int = Query(default=100, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if mode not in SEARCH_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Unbekannter Suchmodus: {mode}",
        )
    filters = SearchFilters(
        source_id=source_id,
        status=status,
        ext=_ext_list(ext),
        modified_after=(
            search_assist.day_start(modified_after) if modified_after else None
        ),
        modified_before=(
            search_assist.day_end(modified_before) if modified_before else None
        ),
        min_size=min_size,
        max_size=max_size,
        is_dir=is_dir,
    )
    try:
        query = build_query(q, mode, _field_list(fields))
    except SearchQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    entry_ids = search_entry_ids(
        db,
        query,
        accessible_scopes(db, user),
        filters=filters,
        ignores=active_rules(db, user) if apply_ignores else [],
        limit=limit,
    )
    return _hits_for(db, user, entry_ids)


# --- Suchassistent ------------------------------------------------------------

@router.post("/assist", response_model=SearchAssistOut)
def assist(
    data: SearchAssistIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Beantwortet eine Frage in Alltagssprache mit einer echten Suche.

    Ablauf: Frage + Kontext (heutiges Datum, Namen der zugänglichen Quellen) an
    das gewählte LLM-Setting, Antwort als JSON einlesen, **validieren** und
    damit die normale Suche ausführen. An das Modell gehen nur die Frage und die
    Quellen-Bezeichnungen – keine Dateilisten, keine Dateiinhalte.
    """
    question = data.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Bitte eine Frage eingeben")

    setting = db.get(LLMSetting, data.setting_id)
    if setting is None or setting.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="LLM-Setting nicht gefunden")
    conn = db.get(LLMConnection, setting.connection_id)
    if conn is None or conn.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="LLM-Verbindung nicht gefunden")

    scopes = accessible_scopes(db, user)
    source_ids = {s.source_id for s in scopes}
    sources = [
        (s.id, s.label)
        for s in db.scalars(select(Source).where(Source.id.in_(source_ids))).all()
    ]

    instruction = search_assist.build_instruction(sources, date.today())
    if data.prompt_id is not None:
        prompt = db.get(LLMPrompt, data.prompt_id)
        if prompt is None or prompt.owner_user_id != user.id:
            raise HTTPException(status_code=404, detail="Prompt nicht gefunden")
        if prompt.body.strip():
            instruction += f"\n\nZusätzliche Hinweise:\n{prompt.body.strip()}"

    prompt_body = f"{instruction}\n\nFrage: {{{{input}}}}"
    try:
        raw = service.run_completion(
            connection=conn,
            model=setting.model,
            system_prompt=setting.system_prompt,
            params=json_obj(setting.params_json),
            prompt_body=prompt_body,
            input_text=question,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        parsed = search_assist.coerce(search_assist.extract_json(raw), source_ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Antwort des Modells nicht verwertbar: {exc}",
        ) from exc

    entry_ids = search_entry_ids(
        db,
        build_query(parsed.query),
        scopes,
        filters=parsed.filters,
        # Auch der Assistent respektiert die Ignorierregeln – sonst tauchte im
        # Assistenten-Ergebnis wieder auf, was der Nutzer bewusst ausblendet.
        ignores=active_rules(db, user),
        limit=data.limit,
    )
    f = parsed.filters
    return SearchAssistOut(
        filters=SearchFiltersOut(
            query=parsed.query,
            source_id=f.source_id,
            source_label=next(
                (label for sid, label in sources if sid == f.source_id), None
            ),
            status=f.status,
            ext=f.ext,
            modified_after=parsed.modified_after,
            modified_before=parsed.modified_before,
            min_size=f.min_size,
            max_size=f.max_size,
            is_dir=f.is_dir,
        ),
        explanation=parsed.explanation,
        hits=_hits_for(db, user, entry_ids),
    )
