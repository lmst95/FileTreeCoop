"""Tests für den LLM-Kern: Token-Krypto, Prompt-Rendering, URL-Guard und die
Provider-Adapter (mit gemocktem httpx-Transport – keine echten Netz-Calls)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm import crypto
from app.llm.providers import ProviderConfig, build_provider
from app.llm.providers.base import LLMError, Provider
from app.llm.service import (
    PLACEHOLDER,
    config_from_connection,
    guard_url,
    render_prompt,
)


# --- Krypto ------------------------------------------------------------------

def test_crypto_roundtrip():
    tok = "sk-abc-123456"
    enc = crypto.encrypt(tok)
    assert enc.startswith("v1:")
    assert tok not in enc  # nicht im Klartext
    assert crypto.decrypt(enc) == tok


def test_crypto_tamper_detected():
    enc = crypto.encrypt("secret")
    with pytest.raises(crypto.TokenDecryptError):
        crypto.decrypt(enc[:-4] + "AAAA")


def test_crypto_optional_helpers():
    assert crypto.encrypt_optional("") is None
    assert crypto.encrypt_optional(None) is None
    assert crypto.decrypt_optional(None) is None
    enc = crypto.encrypt_optional("x")
    assert crypto.decrypt_optional(enc) == "x"


def test_config_from_connection_maps_key_mismatch_to_llm_error():
    """Passt der Schlüssel nicht mehr, muss ein LLMError (502) statt eines 500ers kommen."""
    class _Conn:
        label = "OpenAI"
        provider_type = "openai"
        base_url = "https://api.openai.com/v1"
        api_key_enc = crypto.encrypt("sk-alt")
        extra_json = None

    from app.config import settings
    original = settings.encryption_key
    settings.encryption_key = original + "-anders"
    try:
        with pytest.raises(LLMError) as exc:
            config_from_connection(_Conn())
    finally:
        settings.encryption_key = original
    assert "neu speichern" in str(exc.value)


# --- Prompt-Rendering --------------------------------------------------------

def test_render_prompt_placeholder():
    assert render_prompt(f"Fasse zusammen: {PLACEHOLDER}", "TEXT") == "Fasse zusammen: TEXT"


def test_render_prompt_append_when_no_placeholder():
    assert render_prompt("Bitte korrigieren.", "TEXT") == "Bitte korrigieren.\n\nTEXT"


def test_render_prompt_empty_body_returns_input():
    assert render_prompt("", "TEXT") == "TEXT"


# --- URL-Guard ---------------------------------------------------------------

def test_guard_url_rejects_bad_scheme():
    with pytest.raises(LLMError):
        guard_url("ftp://example.com")


def test_guard_url_accepts_https():
    guard_url("https://api.openai.com/v1")  # wirft nicht


def test_guard_url_blocks_private_host_when_enabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "llm_block_private_hosts", True)
    with pytest.raises(LLMError):
        guard_url("http://127.0.0.1:11434/v1")
    # Bei ausgeschaltetem Schutz (Default) ist localhost erlaubt.
    monkeypatch.setattr(settings, "llm_block_private_hosts", False)
    guard_url("http://127.0.0.1:11434/v1")


# --- Provider-Adapter (gemockter Transport) ----------------------------------

def _mock(provider, handler):
    """Ersetzt den HTTP-Client des Providers durch einen MockTransport."""
    def _client():
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    provider._client = _client  # type: ignore[method-assign]
    return provider


def test_openai_chat_and_models():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            seen["auth"] = request.headers.get("Authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Hallo Welt"}}]
            })
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})
        return httpx.Response(404)

    p = _mock(build_provider(ProviderConfig(
        "openai", "https://api.openai.com/v1", api_key="sk-test")), handler)

    out = p.chat(system="Sei knapp.", user="Hi", model="gpt-4o", params={"temperature": 0.2})
    assert out == "Hallo Welt"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "Sei knapp."}
    assert seen["body"]["temperature"] == 0.2

    assert p.list_models() == ["gpt-4o", "gpt-4o-mini"]


def test_anthropic_chat_uses_system_and_default_max_tokens():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}]})

    p = _mock(build_provider(ProviderConfig(
        "anthropic", "https://api.anthropic.com", api_key="ak-test")), handler)

    out = p.chat(system="Systemtext", user="Frage", model="claude-sonnet-5", params={})
    assert out == "OK"
    assert seen["headers"]["x-api-key"] == "ak-test"
    assert seen["headers"]["anthropic-version"]
    assert seen["body"]["system"] == "Systemtext"
    assert seen["body"]["max_tokens"] == 1024  # Pflichtfeld -> Default gesetzt
    assert seen["body"]["messages"] == [{"role": "user", "content": "Frage"}]


def test_ollama_chat_and_models():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "Servus"}})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3"}, {"name": "mistral"}]})
        return httpx.Response(404)

    p = _mock(build_provider(ProviderConfig("ollama", "http://localhost:11434")), handler)
    assert p.chat(system="", user="Hi", model="llama3", params={}) == "Servus"
    assert p.list_models() == ["llama3", "mistral"]


def test_openai_retries_with_max_completion_tokens():
    """Neuere Modelle lehnen max_tokens ab -> Adapter benennt um und wiederholt."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message":
                "Unsupported parameter: 'max_tokens' is not supported with this model. "
                "Use 'max_completion_tokens' instead."}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    p = _mock(build_provider(ProviderConfig(
        "openai", "https://api.openai.com/v1", api_key="sk")), handler)
    out = p.chat(system="", user="Hi", model="gpt-5", params={"max_tokens": 500})
    assert out == "OK"
    assert len(calls) == 2
    assert "max_tokens" not in calls[1]
    assert calls[1]["max_completion_tokens"] == 500


def test_openai_retry_drops_unsupported_temperature():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message":
                "Unsupported value: 'temperature' does not support 0.3 with this model."}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    p = _mock(build_provider(ProviderConfig(
        "openai", "https://api.openai.com/v1", api_key="sk")), handler)
    out = p.chat(system="", user="Hi", model="o3", params={"temperature": 0.3})
    assert out == "OK"


def test_openai_empty_content_length_raises_helpful_error():
    """gpt-5 & Co. liefern bei erschöpftem Budget leeren Content -> klare Meldung."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}]
        })

    p = _mock(build_provider(ProviderConfig(
        "openai", "https://api.openai.com/v1", api_key="sk")), handler)
    with pytest.raises(LLMError) as exc:
        p.chat(system="", user="Hi", model="gpt-5-nano", params={"max_tokens": 50})
    assert "Token-Limit" in str(exc.value)


def test_openai_empty_content_other_reason_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None}, "finish_reason": "stop"}]
        })

    p = _mock(build_provider(ProviderConfig(
        "openai", "https://api.openai.com/v1", api_key="sk")), handler)
    with pytest.raises(LLMError) as exc:
        p.chat(system="", user="Hi", model="gpt-4o", params={})
    assert "leere Antwort" in str(exc.value)


def test_http_error_is_mapped_to_llmerror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    p = _mock(build_provider(ProviderConfig(
        "openai", "https://api.openai.com/v1", api_key="bad")), handler)
    with pytest.raises(LLMError) as exc:
        p.chat(system="", user="Hi", model="gpt-4o", params={})
    assert "Invalid API key" in str(exc.value)


def test_unknown_provider_type():
    with pytest.raises(LLMError):
        build_provider(ProviderConfig("nope", "https://x"))
