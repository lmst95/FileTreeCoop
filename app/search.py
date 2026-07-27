"""FTS5-Suchlogik: baut aus einem Freitext-String eine tolerante MATCH-Query.

Neben dem Volltext kennt die Suche strukturelle Filter (Endung, Änderungsdatum,
Größe, Datei/Ordner). Sie funktioniert deshalb auch **ohne** Suchtext – etwa für
„alle PDFs über 100 MB, die seit zwei Jahren niemand angefasst hat“. Genau diese
Filter füllt der Suchassistent aus einer Frage in Alltagssprache.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.access import Scope, like_escape

# Zeichen, die FTS5 als Syntax interpretiert; wir entschärfen die Eingabe, indem
# wir jedes Token in Anführungszeichen setzen und als Präfix (*) matchen.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Die Spalten des FTS5-Index – zugleich die möglichen Suchbereiche.
SEARCH_FIELDS: tuple[str, ...] = ("name", "path", "notes")


@dataclass
class SearchFilters:
    """Strukturelle Einschränkungen der Suche (alles optional)."""

    source_id: int | None = None
    status: str | None = None  # present | missing
    ext: list[str] = field(default_factory=list)  # ohne Punkt, kleingeschrieben
    modified_after: float | None = None  # epoch-Sekunden
    modified_before: float | None = None
    min_size: int | None = None
    max_size: int | None = None
    is_dir: bool | None = None

    def is_empty(self) -> bool:
        return not any(
            [
                self.source_id is not None,
                self.status is not None,
                bool(self.ext),
                self.modified_after is not None,
                self.modified_before is not None,
                self.min_size is not None,
                self.max_size is not None,
                self.is_dir is not None,
            ]
        )


def clean_fields(fields: list[str] | None) -> list[str]:
    """Behält nur echte Index-Spalten, in der Reihenfolge des Index, ohne Dubletten."""
    if not fields:
        return []
    wanted = set(fields)
    return [f for f in SEARCH_FIELDS if f in wanted]


def build_match_query(user_input: str, fields: list[str] | None = None) -> str:
    """Wandelt Freitext in eine FTS5-MATCH-Query mit Präfix-Matching um.

    "angebot kunde" -> '"angebot"* "kunde"*'  (implizites UND über alle Spalten)

    Mit ``fields`` lässt sich der Suchbereich einschränken – etwa nur auf den
    Dateinamen, damit ein Ordner namens „Angebote“ nicht jede Datei darunter zum
    Treffer macht:

    ["name"] -> '{name} : "angebot"* {name} : "kunde"*'

    Gibt "" zurück, wenn keine verwertbaren Tokens vorhanden sind.
    """
    tokens = _TOKEN_RE.findall(user_input or "")
    if not tokens:
        return ""
    cols = clean_fields(fields)
    # Alle Spalten = keine Einschränkung; das spart FTS5 den Spaltenfilter.
    prefix = "{" + " ".join(cols) + "} : " if cols and len(cols) < len(SEARCH_FIELDS) else ""
    # Innere Anführungszeichen kann es dank Tokenizer nicht geben, aber sicher ist sicher.
    return " ".join(f'{prefix}"{t}"*' for t in tokens)


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


def _filter_sql(filters: SearchFilters, params: dict[str, object]) -> list[str]:
    """Übersetzt die strukturellen Filter in WHERE-Fragmente (füllt params)."""
    where: list[str] = []
    if filters.source_id is not None:
        where.append("e.source_id = :only_source")
        params["only_source"] = filters.source_id
    if filters.status is not None:
        where.append("e.status = :status")
        params["status"] = filters.status
    if filters.ext:
        names = []
        for i, value in enumerate(filters.ext):
            key = f"ext{i}"
            params[key] = value
            names.append(f":{key}")
        where.append(f"e.ext IN ({', '.join(names)})")
    if filters.modified_after is not None:
        where.append("e.mtime >= :mt_after")
        params["mt_after"] = filters.modified_after
    if filters.modified_before is not None:
        where.append("e.mtime <= :mt_before")
        params["mt_before"] = filters.modified_before
    if filters.min_size is not None:
        where.append("e.size >= :min_size")
        params["min_size"] = filters.min_size
    if filters.max_size is not None:
        where.append("e.size <= :max_size")
        params["max_size"] = filters.max_size
    if filters.is_dir is not None:
        where.append("e.is_dir = :is_dir")
        params["is_dir"] = 1 if filters.is_dir else 0
    return where


def search_entry_ids(
    db: Session,
    match_query: str,
    scopes: list[Scope],
    *,
    filters: SearchFilters | None = None,
    limit: int = 100,
) -> list[int]:
    """Liefert Entry-IDs, auf zugängliche Scopes gefiltert.

    Mit Suchtext entscheidet die FTS-Relevanz (bm25) über die Reihenfolge; ohne
    Suchtext – also rein strukturell – kommen die zuletzt geänderten Dateien
    zuerst. Ohne beides bleibt das Ergebnis leer (kein „alles ausgeben“).
    """
    filters = filters or SearchFilters()
    if not scopes:
        return []
    if not match_query and filters.is_empty():
        return []

    params: dict[str, object] = {"limit": limit}
    where = [_scope_sql(scopes, params), *_filter_sql(filters, params)]

    if match_query:
        params["q"] = match_query
        where.insert(0, "entries_fts MATCH :q")
        sql = f"""
            SELECT e.id
            FROM entries_fts f
            JOIN entries e ON e.id = f.rowid
            WHERE {' AND '.join(where)}
            ORDER BY bm25(entries_fts, 10.0, 3.0, 5.0)
            LIMIT :limit
        """
    else:
        sql = f"""
            SELECT e.id
            FROM entries e
            WHERE {' AND '.join(where)}
            ORDER BY e.mtime DESC, e.id DESC
            LIMIT :limit
        """
    rows = db.execute(text(sql), params).all()
    return [r[0] for r in rows]
