"""Annotationen: Notizen, Todos, Labels und Übergaben an Dateien."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.access import (
    accessible_scopes,
    best_permission,
    can_annotate,
    path_in_scope,
    scope_entry_condition,
)
from app.auth import find_user, get_current_user
from app.db import get_db
from app.models import Annotation, AnnotationShare, Entry, Source, SourceShare, User
from app.schemas import (
    AnnotationIn,
    AnnotationOut,
    AnnotationRich,
    AnnotationShareIn,
    AnnotationShareOut,
    LabelCount,
    NoteOut,
)
from app.serializers import annotation_out, annotations_out

router = APIRouter(prefix="/api/annotations", tags=["annotations"])

_VALID_TYPES = {"note", "todo", "label", "handover"}


def _member_ids_for_path(db: Session, source: Source, path: str) -> set[int]:
    """Besitzer + Nutzer, deren Freigabe diesen Pfad abdeckt (Übergabe-Empfänger)."""
    ids = {source.owner_user_id}
    for sh in db.scalars(
        select(SourceShare).where(SourceShare.source_id == source.id)
    ).all():
        if path_in_scope(path, sh.path_prefix):
            ids.add(sh.user_id)
    return ids


_VALID_STATUS = {"open", "accepted", "done"}


class AnnotationPatch(BaseModel):
    body: str | None = None
    label_value: str | None = None
    assignee_user_id: int | None = None
    done: bool | None = None
    # Übergabe-Workflow: open | accepted | done.
    status: str | None = None
    # ``null`` löscht die Fälligkeit – deshalb zählt hier die Anwesenheit des
    # Feldes, nicht sein Wert (siehe ``model_fields_set`` in update_annotation).
    due_date: date | None = None
    color: str | None = None


def _entry_with_access(
    db: Session, user: User, entry_id: int, *, need_annotate: bool
) -> Entry:
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    scopes = accessible_scopes(db, user)
    perm = best_permission(scopes, entry.source_id, entry.path)
    if perm is None:
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Eintrag")
    if need_annotate and not can_annotate(perm):
        raise HTTPException(status_code=403, detail="Nur Lesezugriff auf diesen Eintrag")
    return entry


@router.post("", response_model=AnnotationOut, status_code=201)
def create_annotation(
    data: AnnotationIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Unbekannter Typ: {data.type}")

    if data.entry_id is None:
        # Freie Notiz (Pinnwand, kein Datei-Bezug) – keine Zugriffsprüfung
        # nötig, der Autor legt einfach an; Sichtbarkeit regeln AnnotationShares.
        if data.type != "note":
            raise HTTPException(
                status_code=422, detail="Nur Notizen können frei (ohne Datei) sein"
            )
        if data.parent_annotation_id is not None:
            raise HTTPException(
                status_code=422, detail="Antworten brauchen einen Datei-Bezug"
            )
    else:
        entry = _entry_with_access(db, user, data.entry_id, need_annotate=True)

        if data.type == "handover":
            if data.assignee_user_id is None:
                raise HTTPException(status_code=422, detail="Übergabe braucht einen Empfänger")
            source = db.get(Source, entry.source_id)
            if data.assignee_user_id not in _member_ids_for_path(db, source, entry.path):
                raise HTTPException(
                    status_code=422,
                    detail="Empfänger hat keinen Zugriff auf diesen Eintrag",
                )

        if data.parent_annotation_id is not None:
            parent = db.get(Annotation, data.parent_annotation_id)
            if parent is None or parent.entry_id != data.entry_id:
                raise HTTPException(
                    status_code=422,
                    detail="Antwort-Bezug ungültig (Annotation gehört nicht zu diesem Eintrag)",
                )
            if parent.parent_annotation_id is not None:
                raise HTTPException(
                    status_code=422, detail="Antworten auf Antworten sind nicht möglich"
                )
            if data.type != "note":
                raise HTTPException(status_code=422, detail="Antworten sind immer Notizen")

    ann = Annotation(
        entry_id=data.entry_id,
        author_user_id=user.id,
        type=data.type,
        body=data.body,
        label_value=data.label_value,
        assignee_user_id=data.assignee_user_id,
        due_date=data.due_date,
        parent_annotation_id=data.parent_annotation_id,
        color=data.color,
    )
    db.add(ann)
    db.commit()
    db.refresh(ann)
    return annotation_out(db, ann)


@router.get("", response_model=list[AnnotationRich])
def list_annotations(
    type: str | None = Query(default=None, description="note|todo|label|handover"),
    source_id: int | None = None,
    label: str | None = Query(default=None, description="exakter Label-Wert"),
    assignee: str | None = Query(default=None, description="'me' oder User-ID"),
    author: str | None = Query(default=None, description="'me' oder User-ID"),
    done: bool | None = Query(default=None, description="Todos: erledigt-Status"),
    q: str | None = Query(default=None, description="Freitext in Notiz/Label/Datei"),
    due_from: date | None = Query(default=None, description="fällig ab (inklusive)"),
    due_to: date | None = Query(default=None, description="fällig bis (inklusive)"),
    has_due: bool | None = Query(default=None, description="nur mit/ohne Termin"),
    order: str = Query(default="updated", description="updated|due"),
    limit: int = Query(default=200, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle Annotationen über die zugänglichen Scopes – für die Übersichtsseite."""
    scopes = accessible_scopes(db, user)
    if not scopes:
        return []

    stmt = (
        select(Annotation, Entry, Source)
        .join(Entry, Entry.id == Annotation.entry_id)
        .join(Source, Source.id == Entry.source_id)
        .where(scope_entry_condition(scopes))
    )
    if type is not None:
        stmt = stmt.where(Annotation.type == type)
    if source_id is not None:
        stmt = stmt.where(Entry.source_id == source_id)
    if label is not None:
        stmt = stmt.where(Annotation.label_value == label)
    if assignee is not None:
        target = user.id if assignee == "me" else int(assignee)
        stmt = stmt.where(Annotation.assignee_user_id == target)
    if author is not None:
        target = user.id if author == "me" else int(author)
        stmt = stmt.where(Annotation.author_user_id == target)
    if done is not None:
        stmt = stmt.where(Annotation.done == done)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Annotation.body.like(like),
                Annotation.label_value.like(like),
                Entry.name.like(like),
                Entry.path.like(like),
            )
        )
    if due_from is not None:
        stmt = stmt.where(Annotation.due_date >= due_from)
    if due_to is not None:
        stmt = stmt.where(Annotation.due_date <= due_to)
    if has_due is not None:
        stmt = stmt.where(
            Annotation.due_date.is_not(None) if has_due
            else Annotation.due_date.is_(None)
        )

    if order == "due":
        # Terminlose ans Ende, sonst aufsteigend nach Fälligkeit.
        stmt = stmt.order_by(
            Annotation.due_date.is_(None), Annotation.due_date, Annotation.created_at
        )
    else:
        stmt = stmt.order_by(Annotation.updated_at.desc())
    stmt = stmt.limit(limit)
    rows = db.execute(stmt).all()

    # Empfängernamen in einem Batch auflösen.
    anns = [r[0] for r in rows]
    outs = {a.id: o for a, o in zip(anns, annotations_out(db, anns))}
    result: list[AnnotationRich] = []
    for ann, entry, source in rows:
        base = outs[ann.id]
        result.append(
            AnnotationRich(
                **base.model_dump(),
                entry_name=entry.name,
                entry_path=entry.path,
                entry_status=entry.status,
                source_id=source.id,
                source_label=source.label,
            )
        )
    return result


@router.get("/notes", response_model=list[NoteOut])
def list_notes(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Alle Notizen für die Pinnwand: an Dateien geheftete (über die
    zugänglichen Scopes) + freie (eigene oder mit mir geteilte)."""
    results: list[NoteOut] = []

    scopes = accessible_scopes(db, user)
    if scopes:
        rows = db.execute(
            select(Annotation, Entry, Source)
            .join(Entry, Entry.id == Annotation.entry_id)
            .join(Source, Source.id == Entry.source_id)
            .where(scope_entry_condition(scopes), Annotation.type == "note")
        ).all()
        anns = [r[0] for r in rows]
        outs = {a.id: o for a, o in zip(anns, annotations_out(db, anns))}
        for ann, entry, source in rows:
            base = outs[ann.id]
            results.append(
                NoteOut(
                    **base.model_dump(),
                    entry_name=entry.name,
                    entry_path=entry.path,
                    entry_status=entry.status,
                    source_id=source.id,
                    source_label=source.label,
                    is_mine=ann.author_user_id == user.id,
                )
            )

    shared_ann_ids = set(
        db.scalars(
            select(AnnotationShare.annotation_id).where(
                AnnotationShare.user_id == user.id
            )
        ).all()
    )
    free_conditions = [Annotation.author_user_id == user.id]
    if shared_ann_ids:
        free_conditions.append(Annotation.id.in_(shared_ann_ids))
    free_anns = db.scalars(
        select(Annotation).where(
            Annotation.entry_id.is_(None), or_(*free_conditions)
        )
    ).all()
    if free_anns:
        share_counts: dict[int, int] = {}
        mine_ids = [a.id for a in free_anns if a.author_user_id == user.id]
        if mine_ids:
            for aid, cnt in db.execute(
                select(AnnotationShare.annotation_id, func.count(AnnotationShare.id))
                .where(AnnotationShare.annotation_id.in_(mine_ids))
                .group_by(AnnotationShare.annotation_id)
            ).all():
                share_counts[aid] = cnt
        outs = {a.id: o for a, o in zip(free_anns, annotations_out(db, list(free_anns)))}
        for ann in free_anns:
            base = outs[ann.id]
            results.append(
                NoteOut(
                    **base.model_dump(),
                    is_mine=ann.author_user_id == user.id,
                    share_count=share_counts.get(ann.id, 0),
                )
            )

    results.sort(key=lambda n: n.updated_at, reverse=True)
    return results


@router.get("/labels", response_model=list[LabelCount])
def list_labels(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vorhandene Label-Werte mit Häufigkeit (für Filter-Chips)."""
    scopes = accessible_scopes(db, user)
    if not scopes:
        return []
    rows = db.execute(
        select(Annotation.label_value, func.count(Annotation.id))
        .join(Entry, Entry.id == Annotation.entry_id)
        .where(
            scope_entry_condition(scopes),
            Annotation.type == "label",
            Annotation.label_value != "",
        )
        .group_by(Annotation.label_value)
        .order_by(func.count(Annotation.id).desc(), Annotation.label_value)
    ).all()
    return [LabelCount(value=v, count=c) for v, c in rows]


@router.get("/by-entry/{entry_id}", response_model=list[AnnotationOut])
def list_for_entry(
    entry_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _entry_with_access(db, user, entry_id, need_annotate=False)
    anns = db.scalars(
        select(Annotation)
        .where(Annotation.entry_id == entry_id)
        .order_by(Annotation.created_at)
    ).all()
    return annotations_out(db, list(anns))


@router.patch("/{annotation_id}", response_model=AnnotationOut)
def update_annotation(
    annotation_id: int,
    patch: AnnotationPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ann = db.get(Annotation, annotation_id)
    if ann is None:
        raise HTTPException(status_code=404, detail="Annotation nicht gefunden")

    if ann.entry_id is None:
        # Freie Notiz: kein Datei-Bezug -> nur der Autor darf sie bearbeiten.
        if ann.author_user_id != user.id:
            raise HTTPException(status_code=403, detail="Nur der Autor darf das")
    else:
        # Der Empfänger einer Übergabe darf Status/Erledigt/Termin auch dann
        # ändern, wenn seine Freigabe nur Lesen erlaubt – es ist *seine* Aufgabe.
        is_assignee = ann.assignee_user_id == user.id
        if is_assignee:
            _entry_with_access(db, user, ann.entry_id, need_annotate=False)
            allowed = {"done", "status", "due_date"}
            extra = set(patch.model_fields_set) - allowed
            if extra:
                _entry_with_access(db, user, ann.entry_id, need_annotate=True)
        else:
            _entry_with_access(db, user, ann.entry_id, need_annotate=True)

    if patch.status is not None:
        if patch.status not in _VALID_STATUS:
            raise HTTPException(status_code=422, detail="Status muss open|accepted|done sein")
        ann.status = patch.status
        ann.done = patch.status == "done"
    if patch.body is not None:
        ann.body = patch.body
    if patch.label_value is not None:
        ann.label_value = patch.label_value
    if patch.assignee_user_id is not None:
        ann.assignee_user_id = patch.assignee_user_id
    if patch.done is not None:
        ann.done = patch.done
        # done und Übergabe-Status konsistent halten.
        if patch.done:
            ann.status = "done"
        elif ann.status == "done":
            ann.status = "open"
    if "due_date" in patch.model_fields_set:
        ann.due_date = patch.due_date  # None = Termin entfernen
    if patch.color is not None:
        ann.color = patch.color
    db.commit()
    db.refresh(ann)
    return annotation_out(db, ann)


@router.delete("/{annotation_id}", status_code=204)
def delete_annotation(
    annotation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ann = db.get(Annotation, annotation_id)
    if ann is None:
        raise HTTPException(status_code=404, detail="Annotation nicht gefunden")
    if ann.entry_id is None:
        if ann.author_user_id != user.id:
            raise HTTPException(status_code=403, detail="Nur der Autor darf das")
    else:
        _entry_with_access(db, user, ann.entry_id, need_annotate=True)
    db.delete(ann)
    db.commit()


# --- Freigaben freier Notizen ------------------------------------------------

def _owned_free_note(db: Session, user: User, annotation_id: int) -> Annotation:
    ann = db.get(Annotation, annotation_id)
    if ann is None or ann.entry_id is not None:
        raise HTTPException(status_code=404, detail="Notiz nicht gefunden")
    if ann.author_user_id != user.id:
        raise HTTPException(status_code=403, detail="Nur der Autor darf das")
    return ann


@router.get("/{annotation_id}/shares", response_model=list[AnnotationShareOut])
def list_note_shares(
    annotation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ann = _owned_free_note(db, user, annotation_id)
    shares = db.scalars(
        select(AnnotationShare).where(AnnotationShare.annotation_id == ann.id)
    ).all()
    users = {
        u.id: u
        for u in db.scalars(
            select(User).where(User.id.in_([s.user_id for s in shares]))
        ).all()
    }
    return [
        AnnotationShareOut(
            user_id=s.user_id,
            username=users[s.user_id].username,
            display_name=users[s.user_id].display_name,
            email=users[s.user_id].email,
        )
        for s in shares
        if s.user_id in users
    ]


@router.post(
    "/{annotation_id}/shares", response_model=AnnotationShareOut, status_code=201
)
def add_note_share(
    annotation_id: int,
    data: AnnotationShareIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ann = _owned_free_note(db, user, annotation_id)
    target = find_user(db, data.identifier)
    if target is None:
        raise HTTPException(
            status_code=404, detail="Kein Nutzer mit dieser E-Mail/Username gefunden"
        )
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Die Notiz gehört dir bereits")
    exists = db.scalar(
        select(AnnotationShare).where(
            AnnotationShare.annotation_id == ann.id,
            AnnotationShare.user_id == target.id,
        )
    )
    if exists is None:
        db.add(AnnotationShare(annotation_id=ann.id, user_id=target.id))
        db.commit()
    return AnnotationShareOut(
        user_id=target.id,
        username=target.username,
        display_name=target.display_name,
        email=target.email,
    )


@router.delete("/{annotation_id}/shares/{user_id}", status_code=204)
def remove_note_share(
    annotation_id: int,
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ann = _owned_free_note(db, user, annotation_id)
    share = db.scalar(
        select(AnnotationShare).where(
            AnnotationShare.annotation_id == ann.id,
            AnnotationShare.user_id == user_id,
        )
    )
    if share is None:
        raise HTTPException(status_code=404, detail="Freigabe nicht gefunden")
    db.delete(share)
    db.commit()
