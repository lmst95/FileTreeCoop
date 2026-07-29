"""Ignorierregeln verwalten: was soll dauerhaft aus der Suche verschwinden?

Die Regeln gehören dem Nutzer und wirken auf jede seiner Suchen (siehe
``app/ignores.py``). Sie ändern den Index nicht – wer eine Regel abschaltet oder
löscht, sieht die Einträge sofort wieder.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import accessible_source_ids
from app.auth import get_current_user
from app.db import get_db
from app.ignores import IGNORE_KINDS, normalize_pattern
from app.models import IgnoreRule, Source, User
from app.patterns import PatternError, glob_to_regex
from app.schemas import IgnoreRuleIn, IgnoreRuleOut, IgnoreRulePatch

router = APIRouter(prefix="/api/ignores", tags=["ignores"])


def _labels(db: Session, rules: list[IgnoreRule]) -> dict[int, str]:
    ids = {r.source_id for r in rules if r.source_id is not None}
    if not ids:
        return {}
    return {
        s.id: s.label
        for s in db.scalars(select(Source).where(Source.id.in_(ids))).all()
    }


def _out(rule: IgnoreRule, labels: dict[int, str]) -> IgnoreRuleOut:
    return IgnoreRuleOut(
        id=rule.id,
        kind=rule.kind,
        pattern=rule.pattern,
        source_id=rule.source_id,
        source_label=labels.get(rule.source_id) if rule.source_id else None,
        active=rule.active,
        note=rule.note,
        created_at=rule.created_at,
    )


def _validate(kind: str, pattern: str) -> str:
    """Prüft Art und Muster und gibt das normalisierte Muster zurück."""
    if kind not in IGNORE_KINDS:
        raise HTTPException(
            status_code=422, detail="Art muss „path“ oder „name“ sein"
        )
    value = normalize_pattern(kind, pattern)
    if not value:
        raise HTTPException(status_code=422, detail="Muster darf nicht leer sein")
    try:
        # Übersetzbarkeit jetzt prüfen, nicht erst beim Suchen.
        glob_to_regex(value)
    except PatternError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return value


def _check_source(db: Session, user: User, source_id: int | None) -> None:
    if source_id is None:
        return
    if source_id not in accessible_source_ids(db, user):
        raise HTTPException(status_code=404, detail="Quelle nicht gefunden")


@router.get("", response_model=list[IgnoreRuleOut])
def list_rules(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rules = list(
        db.scalars(
            select(IgnoreRule)
            .where(IgnoreRule.user_id == user.id)
            .order_by(IgnoreRule.id)
        ).all()
    )
    labels = _labels(db, rules)
    return [_out(r, labels) for r in rules]


@router.post("", response_model=IgnoreRuleOut, status_code=201)
def create_rule(
    data: IgnoreRuleIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pattern = _validate(data.kind, data.pattern)
    _check_source(db, user, data.source_id)

    existing = db.scalars(
        select(IgnoreRule).where(
            IgnoreRule.user_id == user.id,
            IgnoreRule.kind == data.kind,
            IgnoreRule.pattern == pattern,
        )
    ).all()
    for rule in existing:
        if rule.source_id == data.source_id:
            # Schon vorhanden: höchstens wieder einschalten, nicht verdoppeln.
            rule.active = True
            db.commit()
            return _out(rule, _labels(db, [rule]))

    rule = IgnoreRule(
        user_id=user.id,
        source_id=data.source_id,
        kind=data.kind,
        pattern=pattern,
        note=data.note.strip(),
        active=True,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _out(rule, _labels(db, [rule]))


@router.patch("/{rule_id}", response_model=IgnoreRuleOut)
def update_rule(
    rule_id: int,
    data: IgnoreRulePatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.get(IgnoreRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")

    if data.pattern is not None:
        rule.pattern = _validate(rule.kind, data.pattern)
    if data.active is not None:
        rule.active = data.active
    if data.note is not None:
        rule.note = data.note.strip()
    # „Anwesenheit zählt“: nur ein ausdrücklich gesendetes Feld ändert die Quelle,
    # sonst ließe sich die Bindung an eine Quelle nie wieder aufheben.
    if "source_id" in data.model_fields_set:
        _check_source(db, user, data.source_id)
        rule.source_id = data.source_id

    db.commit()
    db.refresh(rule)
    return _out(rule, _labels(db, [rule]))


@router.delete("/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.get(IgnoreRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    db.delete(rule)
    db.commit()
    return Response(status_code=204)
