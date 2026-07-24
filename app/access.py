"""Pfadgenaue Zugriffslogik: ein Nutzer hat Zugriff auf eine Menge von *Scopes*.

Ein Scope ist ein (source_id, path_prefix, permission)-Tripel:
- Besitzer:            (sid, "", "owner")        – ganze eigene Quelle
- Ganze Quelle geteilt:(sid, "", "read|annotate")
- Teilbaum geteilt:    (sid, "docs/x", "read|annotate")

Ein Eintrag ist zugänglich, wenn ein Scope ihn abdeckt: prefix == "" (ganze
Quelle) oder der Pfad gleich dem Präfix ist oder unter "präfix/" liegt.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from app.models import Entry, Source, SourceShare, User

# Rangfolge der Berechtigungen (höher = mehr Rechte).
_PERM_RANK = {"read": 1, "annotate": 2, "owner": 3}


@dataclass(frozen=True)
class Scope:
    source_id: int
    path_prefix: str
    permission: str


def like_escape(value: str) -> str:
    """Entschärft LIKE-Sonderzeichen (\\ % _), passend zu ESCAPE '\\'."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def path_in_scope(path: str, prefix: str) -> bool:
    """True, wenn `prefix` ein Vorfahre-oder-gleich von `path` ist."""
    if prefix == "":
        return True
    return path == prefix or path.startswith(prefix + "/")


def accessible_scopes(db: Session, user: User) -> list[Scope]:
    """Alle Scopes des Nutzers (eigene Quellen + Freigaben, ggf. Teilbäume)."""
    scopes: list[Scope] = []
    for sid in db.scalars(
        select(Source.id).where(Source.owner_user_id == user.id)
    ).all():
        scopes.append(Scope(sid, "", "owner"))
    for sh in db.scalars(
        select(SourceShare).where(SourceShare.user_id == user.id)
    ).all():
        scopes.append(Scope(sh.source_id, sh.path_prefix, sh.permission))
    return scopes


def accessible_source_ids(db: Session, user: User) -> list[int]:
    """IDs aller Quellen, auf die der Nutzer irgendeinen Zugriff hat."""
    return list(dict.fromkeys(s.source_id for s in accessible_scopes(db, user)))


def scopes_for_source(scopes: list[Scope], source_id: int) -> list[Scope]:
    return [s for s in scopes if s.source_id == source_id]


def best_permission(scopes: list[Scope], source_id: int, path: str) -> str | None:
    """Höchste Berechtigung eines Scopes, der (source_id, path) abdeckt – oder None."""
    best: str | None = None
    for s in scopes:
        if s.source_id == source_id and path_in_scope(path, s.path_prefix):
            if best is None or _PERM_RANK[s.permission] > _PERM_RANK[best]:
                best = s.permission
    return best


def can_annotate(permission: str | None) -> bool:
    return permission in {"owner", "annotate"}


def scope_entry_condition(scopes: list[Scope]):
    """SQLAlchemy-Bedingung, die Entry auf die zugänglichen Scopes einschränkt."""
    clauses = []
    for s in scopes:
        if s.path_prefix == "":
            clauses.append(Entry.source_id == s.source_id)
        else:
            like = f"{like_escape(s.path_prefix)}/%"
            clauses.append(
                and_(
                    Entry.source_id == s.source_id,
                    or_(Entry.path == s.path_prefix, Entry.path.like(like, escape="\\")),
                )
            )
    return or_(*clauses) if clauses else false()


def shallowest_prefixes(prefixes: list[str]) -> list[str]:
    """Entfernt Präfixe, die unter einem anderen Präfix der Liste liegen."""
    uniq = sorted(set(prefixes), key=len)
    kept: list[str] = []
    for p in uniq:
        if not any(p == q or p.startswith(q + "/") for q in kept):
            kept.append(p)
    return kept
