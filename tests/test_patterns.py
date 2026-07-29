"""Tests für die Muster-Übersetzung (Glob -> Regex) und den Query-Builder."""

from __future__ import annotations

import re

import pytest

from app.patterns import PatternError, check_regex, glob_to_regex, has_glob, regexp
from app.search import SearchQueryError, build_match_query, build_query


def _matches(pattern: str, value: str, *, subtree: bool = False) -> bool:
    rx = re.compile(glob_to_regex(pattern, subtree=subtree), re.IGNORECASE)
    return rx.search(value) is not None


@pytest.mark.parametrize(
    "pattern,value,expected",
    [
        ("*.pdf", "bericht.pdf", True),
        ("*.pdf", "BERICHT.PDF", True),  # Groß-/Kleinschreibung egal
        ("*.pdf", "bericht.pdf.bak", False),  # vollständiges Matching
        ("*.pdf", "Ordner/bericht.pdf", False),  # * überspringt kein "/"
        ("**/tmp", "a/b/tmp", True),
        ("**/tmp", "tmp", True),  # "**/" darf auch leer sein
        ("Archiv/**", "Archiv/2019/x.pdf", True),
        ("Rechnung_20??", "Rechnung_2026", True),
        ("Rechnung_20??", "Rechnung_206", False),
        ("[ab]test", "btest", True),
        ("[!ab]test", "btest", False),
        ("bericht.pdf", "bericht.pdf", True),  # ohne Platzhalter: exakt
        ("bericht(1).pdf", "bericht(1).pdf", True),  # Regex-Zeichen sind Literale
    ],
)
def test_glob_matching(pattern, value, expected):
    assert _matches(pattern, value) is expected


def test_glob_subtree_covers_everything_below():
    assert _matches("Archiv/2019", "Archiv/2019", subtree=True)
    assert _matches("Archiv/2019", "Archiv/2019/alt/x.pdf", subtree=True)
    # Kein Halbtreffer auf Geschwister mit gleichem Präfix.
    assert not _matches("Archiv/2019", "Archiv/2019b/x.pdf", subtree=True)


def test_unclosed_bracket_is_a_literal():
    assert _matches("bericht[1.pdf", "bericht[1.pdf")


def test_has_glob():
    assert has_glob("*.tmp")
    assert not has_glob("Archiv/2019")


def test_overlong_pattern_is_rejected():
    with pytest.raises(PatternError):
        glob_to_regex("x" * 5000)


def test_check_regex_rejects_broken_expression():
    with pytest.raises(PatternError):
        check_regex("(unclosed")
    assert check_regex(r"^\d+$") == r"^\d+$"


def test_regexp_sql_helper_is_lenient():
    """In SQL darf ein kaputtes Muster nicht sprengen – es matcht dann nichts."""
    assert regexp(r"\d+", "abc123") == 1
    assert regexp(r"\d+", None) == 0
    assert regexp("(unclosed", "abc") == 0


def test_build_match_query_operators():
    assert build_match_query("angebot kunde") == '("angebot"*) AND ("kunde"*)'
    assert build_match_query("a OR b") == '("a"* OR "b"*)'
    assert build_match_query('"zwei wörter"') == '("zwei wörter")'
    assert build_match_query("a -b") == '(("a"*)) NOT ("b"*)'
    # Nur Ausschlüsse ergeben keine Query (FTS5 kann kein „alles außer“).
    assert build_match_query("-b") == ""
    assert build_match_query("") == ""


def test_build_match_query_restricts_columns():
    assert build_match_query("angebot", ["name"]) == '{name} : ("angebot"*)'
    # Alle Spalten = kein Spaltenfilter.
    assert "{" not in build_match_query("angebot", ["name", "path", "notes"])


def test_build_query_modes():
    assert build_query("*.pdf", "glob").regex == r"^[^/]*\.pdf$"
    assert build_query("2026_01", "exact").like == r"%2026\_01%"
    assert build_query(r"^a.b$", "regex").regex == r"^a.b$"
    # Leere Eingabe ist erlaubt (dann filtert nur die Struktur).
    assert build_query("", "regex").is_empty()


def test_build_query_rejects_broken_input():
    with pytest.raises(SearchQueryError):
        build_query("(unclosed", "regex")
    with pytest.raises(SearchQueryError):
        build_query("x", "zauberei")
