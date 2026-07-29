"""Ignorierregeln: dauerhaft ausgeblendete Ordner und Dateinamen.

Der Index bleibt vollständig – ausgeblendet wird erst beim Suchen. Das ist
Absicht: eine Regel ist eine Sicht-Einstellung, kein Löschbefehl. Wer sie
zurücknimmt, sieht sofort wieder alles; ein neuer Scan muss dafür nicht laufen.

Eine Regel ist entweder
- ein **Pfad** (``kind == "path"``): der Eintrag selbst und alles darunter.
  Ohne Platzhalter wirkt er als Präfix (schnell, per LIKE), mit Platzhaltern
  als Regex über den ganzen Pfad (``**/node_modules``, ``Archiv/20??``).
- ein **Name** (``kind == "name"``): ein Glob über den Dateinamen, egal wo er
  liegt (``*.tmp``, ``~$*``, ``.DS_Store``).

Beides optional an eine Quelle gebunden (``source_id``); ohne Bindung gilt die
Regel überall.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import like_escape
from app.models import IgnoreRule, User
from app.patterns import glob_to_regex, has_glob

IGNORE_KINDS: tuple[str, ...] = ("path", "name")


def normalize_pattern(kind: str, pattern: str) -> str:
    """Räumt eine Eingabe auf: Leerraum weg, bei Pfaden führende/­folgende ``/``."""
    value = (pattern or "").strip()
    if kind == "path":
        value = value.strip("/")
    return value


def rule_condition(rule: IgnoreRule, params: dict[str, object], idx: int) -> str:
    """SQL-Fragment, das auf die von ``rule`` getroffenen Zeilen passt.

    Der Aufrufer negiert es (siehe ``ignore_sql``); so bleibt die Bedingung hier
    positiv formuliert und damit lesbar.
    """
    parts: list[str] = []
    if rule.source_id is not None:
        key = f"ig_sid{idx}"
        params[key] = rule.source_id
        parts.append(f"e.source_id = :{key}")

    if rule.kind == "name":
        key = f"ig_rx{idx}"
        params[key] = glob_to_regex(rule.pattern)
        parts.append(f"e.name REGEXP :{key}")
    elif has_glob(rule.pattern):
        # Muster über den ganzen Pfad – inklusive allem unterhalb eines Treffers.
        key = f"ig_rx{idx}"
        params[key] = glob_to_regex(rule.pattern, subtree=True)
        parts.append(f"e.path REGEXP :{key}")
    else:
        # Klartext-Pfad: der Eintrag selbst plus sein Unterbaum.
        pkey, lkey = f"ig_p{idx}", f"ig_pl{idx}"
        params[pkey] = rule.pattern
        params[lkey] = f"{like_escape(rule.pattern)}/%"
        parts.append(f"(e.path = :{pkey} OR e.path LIKE :{lkey} ESCAPE '\\')")

    return "(" + " AND ".join(parts) + ")"


def ignore_sql(rules: list[IgnoreRule], params: dict[str, object]) -> list[str]:
    """Übersetzt die Regeln in WHERE-Fragmente (füllt ``params``).

    Ergebnis ist eine Liste von ``NOT (...)`` – jede Regel schließt für sich aus.
    """
    where: list[str] = []
    for i, rule in enumerate(rules):
        if not rule.pattern:
            continue
        where.append("NOT " + rule_condition(rule, params, i))
    return where


def active_rules(db: Session, user: User) -> list[IgnoreRule]:
    """Die aktiven Regeln des Nutzers (leere Liste = nichts ausblenden)."""
    return list(
        db.scalars(
            select(IgnoreRule)
            .where(IgnoreRule.user_id == user.id, IgnoreRule.active.is_(True))
            .order_by(IgnoreRule.id)
        ).all()
    )
