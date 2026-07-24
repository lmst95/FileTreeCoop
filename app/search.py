"""FTS5-Suchlogik: baut aus einem Freitext-String eine tolerante MATCH-Query."""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.access import Scope, like_escape

# Zeichen, die FTS5 als Syntax interpretiert; wir entschärfen die Eingabe, indem
# wir jedes Token in Anführungszeichen setzen und als Präfix (*) matchen.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def build_match_query(user_input: str) -> str:
    """Wandelt Freitext in eine FTS5-MATCH-Query mit Präfix-Matching um.

    "angebot kunde" -> '"angebot"* "kunde"*'  (implizites UND über alle Spalten)
    Gibt "" zurück, wenn keine verwertbaren Tokens vorhanden sind.
    """
    tokens = _TOKEN_RE.findall(user_input or "")
    if not tokens:
        return ""
    # Innere Anführungszeichen kann es dank Tokenizer nicht geben, aber sicher ist sicher.
    return " ".join(f'"{t}"*' for t in tokens)


def _scope_sql(scopes: list[Scope], params: dict[str, object]) -> str:
    """Baut die OR-Bedingung über die zugänglichen Scopes (füllt params)."""
    parts = []
    for i, s in enumerate(scopes):
        if s.path_prefix == "":
            params[f"sid{i}"] = s.source_id
            parts.append(f"e.source_id = :sid{i}")
        else:
            params[f"sid{i}"] = s.source_id
            params[f"pfx{i}"] = s.path_prefix
            params[f"pfxlike{i}"] = f"{like_escape(s.path_prefix)}/%"
            parts.append(
                f"(e.source_id = :sid{i} AND "
                f"(e.path = :pfx{i} OR e.path LIKE :pfxlike{i} ESCAPE '\\'))"
            )
    return "(" + " OR ".join(parts) + ")"


def search_entry_ids(
    db: Session,
    match_query: str,
    scopes: list[Scope],
    *,
    source_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[int]:
    """Liefert Entry-IDs nach FTS-Relevanz (bm25), auf zugängliche Scopes gefiltert."""
    if not match_query or not scopes:
        return []

    params: dict[str, object] = {"q": match_query, "limit": limit}
    where = ["entries_fts MATCH :q", _scope_sql(scopes, params)]
    if source_id is not None:
        where.append("e.source_id = :only_source")
        params["only_source"] = source_id
    if status is not None:
        where.append("e.status = :status")
        params["status"] = status

    sql = f"""
        SELECT e.id
        FROM entries_fts f
        JOIN entries e ON e.id = f.rowid
        WHERE {' AND '.join(where)}
        ORDER BY bm25(entries_fts, 10.0, 3.0, 5.0)
        LIMIT :limit
    """
    rows = db.execute(text(sql), params).all()
    return [r[0] for r in rows]
