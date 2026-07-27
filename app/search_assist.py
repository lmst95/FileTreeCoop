"""Suchassistent: übersetzt eine Frage in Alltagssprache in Suchfilter.

Das Modell bekommt **keine** Dateiinhalte zu sehen – nur die Frage, das heutige
Datum und die Namen der zugänglichen Quellen. Es antwortet mit einem JSON-Objekt,
das hier streng validiert wird; ausgeführt wird anschließend die ganz normale
Suche. Halluziniert das Modell ein Feld, fällt es beim Validieren heraus, statt
in die Query zu wandern.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from app.search import SearchFilters

# Was das Modell liefern soll. Bewusst knapp gehalten: jedes Feld ist optional,
# ``null`` heißt „nicht einschränken“.
_SCHEMA = """{
  "query": "Suchwörter für Name, Pfad, Notizen und Labels (leer, wenn die Frage
            nur strukturell ist)",
  "source_id": null,
  "status": null,
  "ext": [],
  "modified_after": null,
  "modified_before": null,
  "min_size": null,
  "max_size": null,
  "is_dir": null,
  "explanation": "ein Satz, wie du die Frage verstanden hast"
}"""

_RULES = """Regeln:
- Antworte ausschließlich mit diesem JSON-Objekt, ohne Text davor oder danach.
- "query" enthält nur die inhaltlichen Suchwörter – keine Füllwörter, keine
  Zeitangaben, keine Dateiendungen (die gehören in die eigenen Felder).
- "ext" sind Dateiendungen ohne Punkt und kleingeschrieben, z. B. ["pdf","docx"].
- "modified_after"/"modified_before" sind Datumsangaben als "JJJJ-MM-TT" und
  beziehen sich auf das Änderungsdatum der Datei.
- "min_size"/"max_size" sind Größen in Bytes (1 MB = 1048576).
- "status" ist "present" (vorhanden), "missing" (verschwunden) oder null.
- "is_dir" ist true, wenn ausdrücklich nach Ordnern gefragt wird, sonst null.
- "source_id" nur setzen, wenn die Frage eine der genannten Quellen eindeutig
  benennt; sonst null.
- Setze jedes Feld auf null bzw. [], das die Frage nicht hergibt. Rate nicht."""


def build_instruction(sources: list[tuple[int, str]], today: date) -> str:
    """Baut den Anweisungsteil des Prompts (der Rest ist die Frage des Nutzers)."""
    if sources:
        source_lines = "\n".join(f"- {sid}: {label}" for sid, label in sources)
    else:
        source_lines = "- (keine)"
    return (
        "Du übersetzt eine Suchanfrage in Alltagssprache in Suchfilter für einen "
        "Dateiindex. Der Index kennt nur Metadaten (Name, Pfad, Größe, "
        "Änderungsdatum) sowie Notizen und Labels der Nutzer – keine "
        "Dateiinhalte.\n\n"
        f"Heutiges Datum: {today.isoformat()}\n"
        f"Verfügbare Quellen (ID: Bezeichnung):\n{source_lines}\n\n"
        f"Antworte mit genau diesem JSON-Objekt:\n{_SCHEMA}\n\n{_RULES}"
    )


def extract_json(raw: str) -> dict:
    """Holt das JSON-Objekt aus der Antwort – auch aus ```json-Blöcken.

    Wirft ``ValueError``, wenn sich nichts Brauchbares finden lässt.
    """
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Die Antwort enthält kein JSON-Objekt.")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Die Antwort ist kein JSON-Objekt.")
    return data


# --- Einzelne Felder säubern -------------------------------------------------

_EXT_RE = re.compile(r"^[a-z0-9]{1,12}$")


def _as_int(value, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= minimum else None


def _as_date(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def day_start(d: date) -> float:
    return datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp()


def day_end(d: date) -> float:
    return datetime.combine(d, time.max, tzinfo=timezone.utc).timestamp()


def _as_ext_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:20]:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lstrip(".").lower()
        if _EXT_RE.match(cleaned) and cleaned not in out:
            out.append(cleaned)
    return out


@dataclass
class AssistedQuery:
    """Das validierte Ergebnis: Suchtext + Filter + Erklärung des Modells."""

    query: str = ""
    filters: SearchFilters | None = None
    explanation: str = ""
    # Datumsangaben zusätzlich als ISO-Strings, damit das UI zeigen kann,
    # worauf sich der Assistent festgelegt hat.
    modified_after: date | None = None
    modified_before: date | None = None


def coerce(data: dict, allowed_source_ids: set[int]) -> AssistedQuery:
    """Macht aus der Modellantwort geprüfte Filter – Unbekanntes fällt weg."""
    filters = SearchFilters()

    source_id = _as_int(data.get("source_id"))
    if source_id in allowed_source_ids:
        filters.source_id = source_id

    status = data.get("status")
    if isinstance(status, str) and status.strip() in {"present", "missing"}:
        filters.status = status.strip()

    filters.ext = _as_ext_list(data.get("ext"))

    after = _as_date(data.get("modified_after"))
    before = _as_date(data.get("modified_before"))
    # Verdrehte Zeiträume ergeben nie einen Treffer – lieber tauschen.
    if after and before and after > before:
        after, before = before, after
    if after:
        filters.modified_after = day_start(after)
    if before:
        filters.modified_before = day_end(before)

    filters.min_size = _as_int(data.get("min_size"))
    filters.max_size = _as_int(data.get("max_size"))
    if (
        filters.min_size is not None
        and filters.max_size is not None
        and filters.min_size > filters.max_size
    ):
        filters.min_size, filters.max_size = filters.max_size, filters.min_size

    is_dir = data.get("is_dir")
    if isinstance(is_dir, bool):
        filters.is_dir = is_dir

    query = data.get("query")
    explanation = data.get("explanation")
    return AssistedQuery(
        query=query.strip() if isinstance(query, str) else "",
        filters=filters,
        explanation=explanation.strip() if isinstance(explanation, str) else "",
        modified_after=after,
        modified_before=before,
    )
