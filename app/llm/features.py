"""Registry der Features, die LLM-Settings/Prompts konsumieren können.

Ein Feature ist nur ein Schlüssel + Anzeigename. Neue Konsumenten (z. B.
"search", "handover") werden hier ergänzt – der Rest (Zuordnung, Dropdowns,
``/api/llm/run``) funktioniert dann ohne weitere Änderung.
"""

from __future__ import annotations

FEATURES: dict[str, str] = {
    "notes": "Notizen",
    "search": "Suche",
}


def is_valid_feature(key: str) -> bool:
    return key in FEATURES
