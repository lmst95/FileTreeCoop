"""Suchlogik: übersetzt eine Eingabe in eine Datenbank-Abfrage.

Vier **Modi** decken unterschiedliche Arten des Suchens ab:

- ``smart`` – FTS5-Volltext über Name, Pfad und Notizen, tolerant (Präfix,
  Diakritika egal) und mit Operatoren: ``-wort`` schließt aus, ``"..."`` sucht
  eine Wortfolge, ``OR`` verknüpft alternativ.
- ``exact`` – wörtliche Teilzeichenkette. Findet, was der Tokenizer zerlegt
  (``2026_01``, ``v1.2-final``).
- ``glob``  – Platzhalter wie im Dateimanager: ``*.pdf``, ``Rechnung_20??``,
  ``Projekte/**/alt``.
- ``regex`` – regulärer Ausdruck für alles Übrige: ``^IMG_\\d{4}\\.(jpg|png)$``.

Dazu kommen strukturelle Filter (Endung, Änderungsdatum, Größe, Datei/Ordner) –
die Suche funktioniert deshalb auch **ohne** Suchtext, etwa für „alle PDFs über
100 MB, die seit zwei Jahren niemand angefasst hat“. Genau diese Filter füllt
der Suchassistent aus einer Frage in Alltagssprache.

Quer über alles liegen die Ignorierregeln (``app/ignores.py``): dauerhaft
ausgeblendete Ordner und Dateinamen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.access import Scope, like_escape
from app.ignores import ignore_sql
from app.models import IgnoreRule
from app.patterns import PatternError, check_regex, glob_to_regex

# Zeichen, die FTS5 als Syntax interpretiert; wir entschärfen die Eingabe, indem
# wir jedes Token in Anführungszeichen setzen und als Präfix (*) matchen.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Ein Lexem der Smart-Suche: optionales Minus/Ausrufezeichen + Zitat oder Wort.
_LEX_RE = re.compile(r'[-!]*"[^"]*"|\S+')

# Nur groß geschrieben, damit „oder“ als Suchwort erhalten bleibt.
_OR_WORDS = {"OR", "ODER", "|"}
_AND_WORDS = {"AND", "UND", "&"}

# Die Spalten des FTS5-Index – zugleich die möglichen Suchbereiche.
SEARCH_FIELDS: tuple[str, ...] = ("name", "path", "notes")

# Die Suchmodi; ``smart`` ist die Voreinstellung.
SEARCH_MODES: tuple[str, ...] = ("smart", "exact", "glob", "regex")

# Spalten für den Musterabgleich (Modi exact/glob/regex). ``notes`` kommt aus
# dem FTS-Index, wo die Annotationstexte je Eintrag zusammengefasst liegen.
_PATTERN_COLUMNS: dict[str, str] = {
    "name": "e.name",
    "path": "e.path",
    "notes": "f.notes",
}


class SearchQueryError(ValueError):
    """Die Eingabe war für den gewählten Modus nicht verwertbar."""


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


@dataclass
class TextQuery:
    """Der Textteil einer Suche – schon in die Syntax des Modus übersetzt.

    Je nach Modus ist genau eines der drei Felder gefüllt (oder keines, dann
    sucht nur die Filterseite):

    - ``match``: FTS5-MATCH-Ausdruck (Modus ``smart``)
    - ``like``:  LIKE-Muster mit ``%`` (Modus ``exact``)
    - ``regex``: verankerter oder freier Regex (Modi ``glob`` / ``regex``)
    """

    mode: str = "smart"
    match: str = ""
    like: str = ""
    regex: str = ""
    # Spalten, über die gesucht wird (immer gefüllt: Vorgabe = alle).
    fields: list[str] = field(default_factory=lambda: list(SEARCH_FIELDS))

    def is_empty(self) -> bool:
        return not (self.match or self.like or self.regex)


def clean_fields(fields: list[str] | None) -> list[str]:
    """Behält nur echte Index-Spalten, in der Reihenfolge des Index, ohne Dubletten."""
    if not fields:
        return []
    wanted = set(fields)
    return [f for f in SEARCH_FIELDS if f in wanted]


def _col_prefix(fields: list[str] | None) -> str:
    """FTS5-Spaltenfilter (``{name path} : ``) – leer, wenn überall gesucht wird."""
    cols = clean_fields(fields)
    if not cols or len(cols) == len(SEARCH_FIELDS):
        return ""
    return "{" + " ".join(cols) + "} : "


def _fts_phrase(raw: str, *, prefix_match: bool) -> str:
    """Ein Lexem als FTS5-Phrase: ``kunde`` -> ``"kunde"*``, ``a b`` -> ``"a b"``.

    Der Tokenizer zerlegt ohnehin in Wörter; die Anführungszeichen halten alles
    zusammen, was FTS5 sonst als Syntax läse. Präfix-Matching (``*``) ergibt nur
    bei einem einzelnen Wort Sinn – eine zitierte Wortfolge meint die Wortfolge.
    """
    tokens = _TOKEN_RE.findall(raw)
    if not tokens:
        return ""
    star = "*" if prefix_match and len(tokens) == 1 else ""
    return '"' + " ".join(tokens) + '"' + star


def build_match_query(user_input: str, fields: list[str] | None = None) -> str:
    """Wandelt Freitext in eine FTS5-MATCH-Query mit Präfix-Matching um.

    "angebot kunde" -> '("angebot"*) AND ("kunde"*)'  (implizites UND)

    Zusätzlich versteht die Eingabe drei Operatoren:

    - ``-wort`` / ``!wort``   schließt Treffer mit diesem Wort aus
    - ``"zwei wörter"``       sucht die Wortfolge statt der Einzelwörter
    - ``a OR b``              erlaubt Alternativen (``ODER``/``|`` genauso)

    Mit ``fields`` lässt sich der Suchbereich einschränken – etwa nur auf den
    Dateinamen, damit ein Ordner namens „Angebote“ nicht jede Datei darunter zum
    Treffer macht: ``["name"] -> '{name} : ("angebot"*)'``.

    Gibt "" zurück, wenn keine verwertbaren Tokens vorhanden sind. Reine
    Ausschlüsse (nur ``-wort``) zählen als leer – FTS5 kann nicht „alles außer“
    suchen, dafür sind die strukturellen Filter da.
    """
    groups: list[list[str]] = []  # UND über die Gruppen, ODER innerhalb einer
    negatives: list[str] = []
    join_or = False

    for lex in _LEX_RE.findall(user_input or ""):
        if lex in _OR_WORDS:
            join_or = True
            continue
        if lex in _AND_WORDS:
            join_or = False
            continue
        negated = False
        while lex[:1] in ("-", "!"):
            negated = True
            lex = lex[1:]
        quoted = len(lex) >= 2 and lex.startswith('"') and lex.endswith('"')
        phrase = _fts_phrase(lex.strip('"'), prefix_match=not quoted)
        if not phrase:
            continue
        if negated:
            negatives.append(phrase)
        elif join_or and groups:
            groups[-1].append(phrase)
        else:
            groups.append([phrase])
        join_or = False

    if not groups:
        return ""

    prefix = _col_prefix(fields)
    def block(exprs: list[str]) -> str:
        return f'{prefix}({" OR ".join(exprs)})'

    positive = " AND ".join(block(g) for g in groups)
    if not negatives:
        return positive
    # Klammern sind nötig: In FTS5 bindet NOT stärker als AND, ohne sie würde
    # sich der Ausschluss nur auf den letzten Teilausdruck beziehen.
    return f"({positive}) NOT {block(negatives)}"


def build_query(
    user_input: str,
    mode: str = "smart",
    fields: list[str] | None = None,
) -> TextQuery:
    """Übersetzt die Eingabe gemäß Modus in die passende Abfrageform.

    Wirft ``SearchQueryError``, wenn das Muster nicht übersetzbar ist (kaputter
    regulärer Ausdruck, zu lange Eingabe) – der Aufrufer macht daraus eine
    verständliche Fehlermeldung statt eines 500ers.
    """
    if mode not in SEARCH_MODES:
        raise SearchQueryError(f"Unbekannter Suchmodus: {mode}")

    raw = (user_input or "").strip()
    cols = clean_fields(fields) or list(SEARCH_FIELDS)
    if not raw:
        return TextQuery(mode=mode, fields=cols)

    if mode == "smart":
        return TextQuery(mode=mode, match=build_match_query(raw, fields), fields=cols)
    if mode == "exact":
        return TextQuery(mode=mode, like=f"%{like_escape(raw)}%", fields=cols)
    try:
        pattern = glob_to_regex(raw) if mode == "glob" else check_regex(raw)
    except PatternError as exc:
        raise SearchQueryError(str(exc)) from exc
    return TextQuery(mode=mode, regex=pattern, fields=cols)


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


def _pattern_sql(query: TextQuery, params: dict[str, object]) -> str:
    """WHERE-Fragment für die Muster-Modi: ODER über die gewählten Spalten."""
    if query.regex:
        params["rx"] = query.regex
        return "(" + " OR ".join(
            f"{_PATTERN_COLUMNS[c]} REGEXP :rx" for c in query.fields
        ) + ")"
    params["lk"] = query.like
    return "(" + " OR ".join(
        f"{_PATTERN_COLUMNS[c]} LIKE :lk ESCAPE '\\'" for c in query.fields
    ) + ")"


def search_entry_ids(
    db: Session,
    query: TextQuery | str,
    scopes: list[Scope],
    *,
    filters: SearchFilters | None = None,
    ignores: list[IgnoreRule] | None = None,
    limit: int = 100,
) -> list[int]:
    """Liefert Entry-IDs, auf zugängliche Scopes gefiltert.

    ``query`` kommt aus ``build_query``; ein blanker String wird als fertige
    FTS-MATCH-Query verstanden.

    Im Volltextmodus entscheidet die FTS-Relevanz (bm25) über die Reihenfolge;
    sonst – bei Mustern wie rein strukturell – kommen die zuletzt geänderten
    Dateien zuerst. Ohne Suchtext **und** ohne Filter bleibt das Ergebnis leer
    (kein „alles ausgeben“); Ignorierregeln allein lösen also nie eine Suche aus.
    """
    if isinstance(query, str):
        query = TextQuery(match=query)
    filters = filters or SearchFilters()
    if not scopes:
        return []
    if query.is_empty() and filters.is_empty():
        return []

    params: dict[str, object] = {"limit": limit}
    where = [
        _scope_sql(scopes, params),
        *_filter_sql(filters, params),
        *ignore_sql(ignores or [], params),
    ]

    if query.match:
        params["q"] = query.match
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
        if not query.is_empty():
            where.append(_pattern_sql(query, params))
        # Der Join kostet nur, wenn wirklich in Notizen gesucht wird.
        join = (
            "LEFT JOIN entries_fts f ON f.rowid = e.id"
            if not query.is_empty() and "notes" in query.fields
            else ""
        )
        sql = f"""
            SELECT e.id
            FROM entries e
            {join}
            WHERE {' AND '.join(where)}
            ORDER BY e.mtime DESC, e.id DESC
            LIMIT :limit
        """
    rows = db.execute(text(sql), params).all()
    return [r[0] for r in rows]
