"""Adapter für OpenAI und OpenAI-kompatible APIs (vLLM, LM Studio, Ollama-/v1,
Together, Groq, …).

``base_url`` ist die API-Basis inkl. Versionspfad, z. B.
``https://api.openai.com/v1`` oder ``http://localhost:11434/v1``.
"""

from __future__ import annotations

from typing import Any

from app.llm.providers.base import LLMError, Provider, _error_message, _short_http_error

_PARAM_KEYS = ("temperature", "max_tokens", "top_p", "presence_penalty", "frequency_penalty")


def _append_citations(content: str, message: dict[str, Any]) -> str:
    """Hängt die Web-Such-Quellen (``url_citation``-Annotationen) an den Text an.

    Die Chat-Completions-Antwort mit Web-Suche liefert die belegten Fundstellen
    unter ``message.annotations``. Wir sammeln sie eindeutig (nach URL) und
    fügen sie als lesbare „Quellen“-Liste an, damit im Ergebnis sichtbar ist,
    worauf sich die Antwort stützt.
    """
    annotations = message.get("annotations")
    if not isinstance(annotations, list):
        return content
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in annotations:
        if not isinstance(a, dict):
            continue
        cite = a.get("url_citation") if isinstance(a.get("url_citation"), dict) else a
        url = cite.get("url")
        if not isinstance(url, str) or not url or url in seen:
            continue
        seen.add(url)
        title = (cite.get("title") or "").strip() or url
        sources.append((title, url))
    if not sources:
        return content
    lines = "\n".join(f"{i}. {t} — {u}" for i, (t, u) in enumerate(sources, 1))
    return f"{content}\n\nQuellen:\n{lines}"


def _adjust_payload_for_error(resp, payload: dict[str, Any]) -> bool:
    """Passt die Payload an, wenn ein Parameter modellbedingt abgelehnt wurde.

    Neuere OpenAI-Modelle (o-Serie, GPT-5-Familie) verlangen
    ``max_completion_tokens`` statt ``max_tokens`` und unterstützen nur den
    Standard-``temperature``. Wir erkennen genau diese Fälle an der Fehlermeldung
    und korrigieren einmalig – ältere Modelle / OpenAI-kompatible Server bleiben
    unberührt, weil sie den Fehler gar nicht senden. Rückgabe: wurde etwas
    geändert (dann lohnt ein erneuter Versuch)?
    """
    msg = _error_message(resp).lower()
    if not msg:
        return False
    if "max_completion_tokens" in msg and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        return True
    if "temperature" in msg and "unsupported" in msg and "temperature" in payload:
        payload.pop("temperature")
        return True
    if "top_p" in msg and "unsupported" in msg and "top_p" in payload:
        payload.pop("top_p")
        return True
    return False


class OpenAIProvider(Provider):
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        extra = self.config.extra.get("headers")
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    def chat(self, *, system: str, user: str, model: str, params: dict[str, Any]) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload: dict[str, Any] = {"model": model, "messages": messages}
        # Web-Suche: lässt das Modell live im Web recherchieren (nur OpenAI und
        # nur mit suchfähigen Modellen wie gpt-4o-search-preview). Opt-in über das
        # Setting; ``web_search`` ist selbst kein Chat-Parameter, daher separat.
        web_search = bool(params.get("web_search"))
        if web_search:
            payload["web_search_options"] = {}
        for key in _PARAM_KEYS:
            if params.get(key) is not None:
                payload[key] = params[key]

        url = f"{self._base()}/chat/completions"
        headers = self._headers()
        # Bis zu ein paar Versuche: bei modellbedingt abgelehnten Parametern die
        # Payload korrigieren (z. B. max_tokens -> max_completion_tokens) und neu
        # senden. Die Schleife ist beschränkt, ein Fix ändert jeweils die Payload.
        resp = None
        for _ in range(4):
            resp = self._send("POST", url, headers=headers, json=payload)
            if resp.status_code < 400:
                break
            if not _adjust_payload_for_error(resp, payload):
                raise LLMError(_short_http_error(resp))
        if resp is None or resp.status_code >= 400:
            raise LLMError(_short_http_error(resp))

        data = resp.json()
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unerwartetes Antwortformat vom Anbieter") from exc

        if not content.strip():
            # Leerer Inhalt trotz HTTP 200: bei Reasoning-Modellen (o-Serie,
            # GPT-5) verbrauchen interne Reasoning-Tokens oft das gesamte
            # Token-Budget, sodass kein Antworttext mehr übrig bleibt.
            if choice.get("finish_reason") == "length":
                raise LLMError(
                    "Das Modell hat das Token-Limit erreicht, bevor eine Antwort "
                    "entstand. Bei Reasoning-Modellen (o-Serie, GPT-5) zählen die "
                    "internen Reasoning-Tokens mit – erhöhe „Max. Tokens“ deutlich "
                    "(z. B. 4000) oder lass das Feld leer."
                )
            raise LLMError(
                "Der Anbieter hat eine leere Antwort zurückgegeben "
                f"(finish_reason: {choice.get('finish_reason', 'unbekannt')})."
            )
        if web_search:
            content = _append_citations(content, choice.get("message") or {})
        return content

    def list_models(self) -> list[str] | None:
        resp = self._request("GET", f"{self._base()}/models", headers=self._headers())
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        return sorted(ids)
