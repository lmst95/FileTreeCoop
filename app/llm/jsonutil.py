"""Kleine JSON-Helfer für die als Text abgelegten JSON-Felder (SQLite).

Die LLM-Tabellen halten Parameter, Extra-Header und Modell-Cache als JSON-Text.
``json_obj``/``json_dump`` kapseln das tolerante Lesen (kaputt/leer -> ``{}``)
und das kompakte Schreiben (leeres Objekt -> ``""``), damit Router und Service
denselben Umgang teilen statt ihn je zweimal zu definieren.
"""

from __future__ import annotations

import json
from typing import Any


def json_obj(raw: str) -> dict[str, Any]:
    """Parst ein JSON-Objekt aus Text; leere/kaputte Eingabe -> ``{}``."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def json_dump(obj: Any) -> str:
    """Serialisiert ein Objekt; „leer“ (None/{}/…) -> ``""`` statt ``"{}"``."""
    return json.dumps(obj) if obj else ""
