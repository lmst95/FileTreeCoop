"""Standard-Prompts, die jeder Nutzer als Starthilfe bekommt.

Beim Registrieren (siehe ``auth.register``) und auf Knopfdruck („Standard-Prompts
anlegen“ auf der KI-Seite) werden diese Vorlagen angelegt und dem Feature
``notes`` zugeordnet, damit sie sofort im KI-Tab einer Notiz auswählbar sind.

Die Anlage ist **idempotent**: existiert bereits ein Prompt gleichen Namens für
den Nutzer, wird er nicht dupliziert – nur eine fehlende Feature-Zuordnung wird
ergänzt. So dient derselbe Weg zum Erst-Seeding wie zum Wiederherstellen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.jsonutil import json_dump
from app.models import LLMConnection, LLMFeatureLink, LLMPrompt, LLMSetting, User

# Fertig konfiguriertes Web-Suche-Setting (per Knopfdruck aus einer
# OpenAI-Verbindung erzeugt, siehe ``seed_web_search_setting``).
WEB_SEARCH_SETTING_LABEL = "Web-Suche (OpenAI)"
WEB_SEARCH_MODEL = "gpt-4o-search-preview"
WEB_SEARCH_FEATURES: tuple[str, ...] = ("notes",)


@dataclass(frozen=True)
class DefaultPrompt:
    name: str
    description: str
    body: str
    features: tuple[str, ...] = field(default=("notes",))


# Die drei Bausteine: sprachlich überarbeiten · erklären · erweitern/beantworten.
DEFAULT_PROMPTS: tuple[DefaultPrompt, ...] = (
    DefaultPrompt(
        name="Sprache & Grammatik",
        description="Korrigiert Grammatik, Rechtschreibung und Stil, ohne den Sinn zu verändern.",
        body=(
            "Überarbeite den folgenden Text sprachlich: Korrigiere Grammatik, "
            "Rechtschreibung und Zeichensetzung und verbessere Stil und Lesbarkeit. "
            "Behalte Sinn, Aussage und Sprache unverändert bei und erfinde keine "
            "neuen Inhalte. Gib ausschließlich den überarbeiteten Text zurück, ohne "
            "Kommentare oder Erläuterungen.\n\n{{input}}"
        ),
    ),
    DefaultPrompt(
        name="Komplexes erklären",
        description="Ergänzt den Text um verständliche Erklärungen schwieriger Stellen.",
        body=(
            "Ergänze den folgenden Text um Erklärungen: Mache komplexe oder unklare "
            "Stellen verständlich, erläutere Fachbegriffe und füge hilfreiche "
            "Hintergrundinformationen hinzu. Behalte den ursprünglichen Text bei und "
            "kennzeichne deine Ergänzungen klar. Antworte in derselben Sprache wie "
            "der Text.\n\n{{input}}"
        ),
    ),
    DefaultPrompt(
        name="Erweitern & beantworten",
        description="Erweitert den Text und beantwortet darin enthaltene Fragen und Themen.",
        body=(
            "Erweitere den folgenden Text: Beantworte die darin enthaltenen Fragen, "
            "vertiefe die angesprochenen Themen und ergänze relevante, weiterführende "
            "Informationen. Bleibe sachlich und beim Thema und antworte in derselben "
            "Sprache wie der Text.\n\n{{input}}"
        ),
    ),
    DefaultPrompt(
        name="Web-Recherche",
        description="Recherchiert die Frage bzw. das Thema und antwortet mit aktuellen Informationen.",
        body=(
            "Recherchiere zur folgenden Frage bzw. zum folgenden Thema und beantworte "
            "sie fundiert und mit aktuellem Stand. Nenne die wichtigsten Quellen. "
            "Antworte in derselben Sprache wie die Anfrage. Am wirkungsvollsten mit "
            "einem Setting, in dem die Web-Suche aktiviert ist.\n\n{{input}}"
        ),
    ),
)


def seed_default_prompts(db: Session, user: User) -> list[LLMPrompt]:
    """Legt die Standard-Prompts für ``user`` an (idempotent) und verknüpft ihr Feature.

    Committet nicht selbst – der Aufrufer entscheidet über die Transaktion.
    Gibt die zu den Standard-Namen gehörenden Prompts zurück (neu oder bestehend).
    """
    existing = {
        p.name: p
        for p in db.scalars(
            select(LLMPrompt).where(LLMPrompt.owner_user_id == user.id)
        ).all()
    }

    result: list[LLMPrompt] = []
    for spec in DEFAULT_PROMPTS:
        prompt = existing.get(spec.name)
        if prompt is None:
            prompt = LLMPrompt(
                owner_user_id=user.id,
                name=spec.name,
                body=spec.body,
                description=spec.description,
            )
            db.add(prompt)
            db.flush()  # prompt.id für die Feature-Links
        _ensure_feature_links(db, user, "prompt", prompt.id, spec.features)
        result.append(prompt)
    return result


def seed_web_search_setting(
    db: Session, user: User, connection: LLMConnection
) -> LLMSetting:
    """Legt (idempotent) ein fertiges Web-Suche-Setting an der Verbindung an.

    Bindet ein suchfähiges OpenAI-Modell mit aktivierter Web-Suche und ordnet es
    dem Feature ``notes`` zu. Existiert bereits ein Setting mit diesem Label,
    wird es unverändert zurückgegeben (nur eine fehlende Feature-Zuordnung wird
    ergänzt). Committet nicht selbst – das entscheidet der Aufrufer.
    """
    setting = db.scalar(
        select(LLMSetting).where(
            LLMSetting.owner_user_id == user.id,
            LLMSetting.label == WEB_SEARCH_SETTING_LABEL,
        )
    )
    if setting is None:
        setting = LLMSetting(
            owner_user_id=user.id,
            connection_id=connection.id,
            label=WEB_SEARCH_SETTING_LABEL,
            model=WEB_SEARCH_MODEL,
            system_prompt="",
            params_json=json_dump({"web_search": True}),
        )
        db.add(setting)
        db.flush()  # setting.id für die Feature-Links
    _ensure_feature_links(db, user, "setting", setting.id, WEB_SEARCH_FEATURES)
    return setting


def _ensure_feature_links(
    db: Session, user: User, kind: str, ref_id: int, features: tuple[str, ...]
) -> None:
    """Ergänzt fehlende (kind, ref_id, feature)-Zuordnungen, ohne bestehende zu berühren."""
    linked = set(
        db.scalars(
            select(LLMFeatureLink.feature_key).where(
                LLMFeatureLink.kind == kind, LLMFeatureLink.ref_id == ref_id
            )
        ).all()
    )
    for key in features:
        if key not in linked:
            db.add(
                LLMFeatureLink(
                    owner_user_id=user.id,
                    kind=kind,
                    ref_id=ref_id,
                    feature_key=key,
                )
            )
