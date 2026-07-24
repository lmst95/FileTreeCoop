"""Provider-Adapter-Registry.

``build_provider`` bildet einen ``provider_type`` auf den passenden Adapter ab.
Neue Anbieter werden hier durch einen Eintrag in ``_REGISTRY`` ergänzt.
"""

from __future__ import annotations

from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.base import LLMError, Provider, ProviderConfig
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.openai import OpenAIProvider

_REGISTRY: dict[str, type[Provider]] = {
    "openai": OpenAIProvider,
    "openai_compatible": OpenAIProvider,
    "custom": OpenAIProvider,  # Fallback: wie OpenAI-kompatibel behandeln
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
}

# Für UI-Dropdowns: bekannte Typen mit Anzeigename.
PROVIDER_TYPES = [
    {"value": "openai", "label": "OpenAI"},
    {"value": "openai_compatible", "label": "OpenAI-kompatibel (vLLM, LM Studio, …)"},
    {"value": "anthropic", "label": "Anthropic (Claude)"},
    {"value": "ollama", "label": "Ollama (self-hosted)"},
    {"value": "custom", "label": "Custom (OpenAI-kompatibel)"},
]


def build_provider(config: ProviderConfig) -> Provider:
    cls = _REGISTRY.get(config.provider_type)
    if cls is None:
        raise LLMError(f"Unbekannter Provider-Typ: {config.provider_type}")
    return cls(config)


__all__ = [
    "LLMError",
    "Provider",
    "ProviderConfig",
    "build_provider",
    "PROVIDER_TYPES",
]
