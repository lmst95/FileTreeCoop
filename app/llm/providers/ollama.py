"""Adapter für die native Ollama-API (self-hosted).

``base_url`` ist die Server-Basis ohne Versionspfad, z. B.
``http://localhost:11434``. (Wer Ollamas OpenAI-kompatiblen ``/v1``-Modus
nutzen will, wählt stattdessen den Provider-Typ ``openai_compatible``.)
"""

from __future__ import annotations

from typing import Any

from app.llm.providers.base import LLMError, Provider

_DEFAULT_BASE = "http://localhost:11434"


class OllamaProvider(Provider):
    def _base(self) -> str:
        return (self.config.base_url or _DEFAULT_BASE).rstrip("/")

    def chat(self, *, system: str, user: str, model: str, params: dict[str, Any]) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        options: dict[str, Any] = {}
        if params.get("temperature") is not None:
            options["temperature"] = params["temperature"]
        if params.get("top_p") is not None:
            options["top_p"] = params["top_p"]
        if params.get("max_tokens") is not None:
            options["num_predict"] = params["max_tokens"]

        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if options:
            payload["options"] = options

        resp = self._request("POST", f"{self._base()}/api/chat", json=payload)
        data = resp.json()
        try:
            return data["message"]["content"] or ""
        except (KeyError, TypeError) as exc:
            raise LLMError("Unerwartetes Antwortformat vom Anbieter") from exc

    def list_models(self) -> list[str] | None:
        resp = self._request("GET", f"{self._base()}/api/tags")
        data = resp.json()
        items = data.get("models") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        names = [m.get("name") for m in items if isinstance(m, dict) and m.get("name")]
        return sorted(names)
