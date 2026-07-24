"""Serialisierung von Annotationen inkl. aufgelöster Nutzernamen.

Autor (wer hat's geschrieben) und Übergabe-Empfänger werden in einem
Batch-Lookup aufgelöst, damit Listen keine N+1-Queries erzeugen.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Annotation, User
from app.schemas import AnnotationOut


def _user_map(db: Session, annotations: Iterable[Annotation]) -> dict[int, User]:
    """Batch-Lookup: alle beteiligten Nutzer (Autoren + Empfänger)."""
    ids = {a.author_user_id for a in annotations}
    ids |= {a.assignee_user_id for a in annotations if a.assignee_user_id is not None}
    if not ids:
        return {}
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids))).all()}


def _build(a: Annotation, users: dict[int, User]) -> AnnotationOut:
    out = AnnotationOut.model_validate(a)
    author = users.get(a.author_user_id)
    if author is not None:
        out.author_name = author.display_name
        out.author_username = author.username
    if a.assignee_user_id is not None:
        assignee = users.get(a.assignee_user_id)
        out.assignee_name = assignee.display_name if assignee else None
    return out


def annotations_out(db: Session, annotations: list[Annotation]) -> list[AnnotationOut]:
    users = _user_map(db, annotations)
    return [_build(a, users) for a in annotations]


def annotation_out(db: Session, annotation: Annotation) -> AnnotationOut:
    return annotations_out(db, [annotation])[0]


def has_new_annotations(annotations, user_id: int, cutoff) -> bool:
    """Gibt es fremde Annotationen, die neuer als der letzte Besuch sind?

    ``cutoff`` = ``SourceVisit.last_seen_at`` oder None (nie besucht – dann
    zählt jede fremde Annotation als neu).
    """
    return any(
        a.author_user_id != user_id and (cutoff is None or a.created_at > cutoff)
        for a in annotations
    )
