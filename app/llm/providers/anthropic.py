"""Adapter für die Anthropic Messages API.

Besonderheiten gegenüber OpenAI: ``x-api-key``-Header statt Bearer, eine
``anthropic-version``, der System-Prompt ist ein Top-Level-Feld (keine
System-Message) und ``max_tokens`` ist Pflicht.
"""

from __future__ import annotations

from typing import Any

from app.llm.providers.base import LLMError, Provider

_DEFAULT_BASE = "https://api.anthropic.com"
_DEFAULT_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 1024


class AnthropicProvider(Provider):
    def _base(self) -> str:
        return (self.config.base_url or _DEFAULT_BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": str(
                self.config.extra.get("anthropic_version", _DEFAULT_VERSION)
            ),
        }
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
        extra = self.config.extra.get("headers")
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    def chat(self, *, system: str, user: str, model: str, params: dict[str, Any]) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": params.get("max_tokens") or _DEFAULT_MAX_TOKENS,
        }
        if system:
            payload["system"] = system
        if params.get("temperature") is not None:
            payload["temperature"] = params["temperature"]
        if params.get("top_p") is not None:
            payload["top_p"] = params["top_p"]

        resp = self._request(
            "POST", f"{self._base()}/v1/messages",
            headers=self._headers(), json=payload,
        )
        data = resp.json()
        try:
            blocks = data["content"]
            texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            return "".join(texts)
        except (KeyError, TypeError, AttributeError) as exc:
            raise LLMError("Unerwartetes Antwortformat vom Anbieter") from exc

    def list_models(self) -> list[str] | None:
        resp = self._request("GET", f"{self._base()}/v1/models", headers=self._headers())
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        return sorted(ids)
