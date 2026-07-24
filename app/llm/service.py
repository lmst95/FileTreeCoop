"""Provider-unabhängige Fassade für die LLM-Nutzung.

Übersetzt die gespeicherten ORM-Objekte (Verbindung/Setting/Prompt) in einen
Adapter-Aufruf: Token entschlüsseln, ``base_url`` prüfen (SSRF), Prompt rendern,
Provider bauen und ausführen. Der Rest der App spricht nur mit diesem Modul –
nie direkt mit den Adaptern.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from app.config import settings
from app.llm import crypto
from app.llm.jsonutil import json_obj
from app.llm.providers import LLMError, ProviderConfig, build_provider

PLACEHOLDER = "{{input}}"


def render_prompt(body: str, input_text: str) -> str:
    """Setzt ``input_text`` in die Vorlage ein.

    Enthält die Vorlage ``{{input}}``, wird dort ersetzt; sonst wird der Text
    (falls vorhanden) angehängt. Leere Vorlage -> nur der Eingabetext.
    """
    body = body or ""
    if PLACEHOLDER in body:
        return body.replace(PLACEHOLDER, input_text)
    if not body.strip():
        return input_text
    if not input_text:
        return body
    return f"{body}\n\n{input_text}"


def guard_url(base_url: str) -> None:
    """Wirft ``LLMError`` bei ungültigem Schema oder – optional – privaten Hosts.

    Der Private-Host-Block ist per ``FTC_LLM_BLOCK_PRIVATE_HOSTS`` schaltbar und
    standardmäßig **aus**, damit lokale Setups (Ollama auf localhost) laufen.
    """
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise LLMError("Basis-URL muss mit http:// oder https:// beginnen")
    if not parts.hostname:
        raise LLMError("Basis-URL enthält keinen gültigen Host")

    if not settings.llm_block_private_hosts:
        return

    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None)
    except socket.gaierror as exc:
        raise LLMError(f"Host nicht auflösbar: {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise LLMError(
                "Zugriff auf private/lokale Adressen ist gesperrt "
                "(FTC_LLM_BLOCK_PRIVATE_HOSTS=false zum Erlauben)"
            )


def config_from_connection(connection) -> ProviderConfig:
    """Baut die Adapter-Konfiguration aus einer ``LLMConnection`` (Token entschlüsselt)."""
    return ProviderConfig(
        provider_type=connection.provider_type,
        base_url=connection.base_url,
        api_key=crypto.decrypt_optional(connection.api_key_enc),
        extra=json_obj(connection.extra_json),
    )


def list_models(connection) -> list[str] | None:
    """Verfügbare Modelle einer Verbindung – oder ``None``, wenn nicht unterstützt."""
    guard_url(connection.base_url)
    provider = build_provider(config_from_connection(connection))
    return provider.list_models()


def run_completion(
    *,
    connection,
    model: str,
    system_prompt: str,
    params: dict[str, Any],
    prompt_body: str,
    input_text: str,
) -> str:
    """Führt einen Lauf aus und gibt den Antworttext zurück."""
    if not model:
        raise LLMError("Kein Modell ausgewählt")
    guard_url(connection.base_url)
    provider = build_provider(config_from_connection(connection))
    user = render_prompt(prompt_body, input_text)
    return provider.chat(system=system_prompt or "", user=user, model=model, params=params)
