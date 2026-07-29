"""Muster-Hilfen: Glob nach Regex übersetzen, Regex sicher kompilieren.

Sowohl die Suche (Modi „glob“ und „regex“) als auch die Ignorierregeln arbeiten
mit Mustern über Name und Pfad – damit beide dieselbe Sprache sprechen, liegt
die Übersetzung hier zentral.

Glob-Semantik (an .gitignore/rsync angelehnt, aber bewusst schlicht):

- ``*``    beliebig viele Zeichen **außer** ``/``   -> ``*.pdf``
- ``**``   beliebig viele Zeichen **mit** ``/``     -> ``Projekte/**/alt``
- ``**/``  beliebig tiefes Vorgeleitverzeichnis (auch keines) -> ``**/tmp``
- ``?``    genau ein Zeichen außer ``/``
- ``[abc]``/``[!abc]`` Zeichenklasse

Muster matchen immer **vollständig** (implizit verankert): ``*.pdf`` trifft die
Datei ``bericht.pdf``, nicht ``bericht.pdf.bak``. Groß-/Kleinschreibung spielt
keine Rolle – bei Dateinamen ist das die Erwartung, nicht die Ausnahme.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Rein defensiv: ein Muster ist eine Sucheingabe, kein Programm.
MAX_PATTERN_LEN = 500

# Zeichen, die ein Muster zum Glob machen (sonst ist es ein Klartext-Name/Pfad).
_GLOB_CHARS = "*?["


class PatternError(ValueError):
    """Ein Muster ließ sich nicht übersetzen (unbrauchbarer Regex o. Ä.)."""


def has_glob(pattern: str) -> bool:
    """True, wenn das Muster Platzhalter enthält (sonst: Klartext)."""
    return any(c in pattern for c in _GLOB_CHARS)


def glob_to_regex(pattern: str, *, subtree: bool = False) -> str:
    """Übersetzt ein Glob-Muster in einen verankerten Regex.

    Mit ``subtree=True`` deckt der Regex zusätzlich alles **unterhalb** eines
    Treffers ab – ``Archiv/2019`` matcht dann auch ``Archiv/2019/alt/x.pdf``.
    Genau das braucht eine Ordner-Ignorierregel.
    """
    if len(pattern) > MAX_PATTERN_LEN:
        raise PatternError(f"Muster ist zu lang (max. {MAX_PATTERN_LEN} Zeichen)")

    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**/", i):
                # Beliebig tiefes (auch leeres) Vorgeleitverzeichnis.
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        if c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":  # "]" direkt am Anfang ist ein Literal
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:  # keine schließende Klammer -> als Literal behandeln
                out.append(r"\[")
                i += 1
                continue
            inner = pattern[i + 1 : j].replace("\\", "\\\\")
            if inner[:1] in ("!", "^"):
                inner = "^" + inner[1:]
            out.append(f"[{inner}]")
            i = j + 1
            continue
        out.append(re.escape(c))
        i += 1

    body = "".join(out)
    tail = "(?:/.*)?" if subtree else ""
    return f"^{body}{tail}$"


def check_regex(pattern: str) -> str:
    """Prüft einen Regex und gibt ihn unverändert zurück (sonst PatternError)."""
    if len(pattern) > MAX_PATTERN_LEN:
        raise PatternError(f"Ausdruck ist zu lang (max. {MAX_PATTERN_LEN} Zeichen)")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise PatternError(f"Ungültiger regulärer Ausdruck: {exc}") from exc
    return pattern


@lru_cache(maxsize=512)
def compiled(pattern: str) -> re.Pattern[str] | None:
    """Kompiliert (gecacht) für die SQLite-Funktion ``REGEXP``.

    None bei kaputtem Muster: SQL ist kein Ort für Exceptions – die Validierung
    passiert beim Anlegen der Suche bzw. der Regel.
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def regexp(pattern: str, value: str | None) -> int:
    """Implementierung des SQL-Operators ``value REGEXP pattern``."""
    if value is None:
        return 0
    rx = compiled(pattern)
    return 1 if rx is not None and rx.search(value) else 0
