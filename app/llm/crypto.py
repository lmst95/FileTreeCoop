"""Verschleierung gespeicherter LLM-API-Tokens – nur mit der Standardbibliothek.

Bewusste Einordnung: Dies ist **keine** auditierte Verschlüsselung wie Fernet,
sondern eine solide, schlüsselgebundene Verschleierung, damit Tokens nicht im
Klartext in der SQLite-Datei liegen. Konstruktion:

    keystream = HMAC-SHA256(k_enc, nonce || counter)   (CTR-artig, blockweise)
    ciphertext = plaintext XOR keystream
    tag = HMAC-SHA256(k_mac, nonce || ciphertext)[:16]  (Integrität)

``k_enc``/``k_mac`` werden per HMAC aus ``settings.encryption_key`` abgeleitet;
je Datensatz sorgt ein zufälliger 16-Byte-Nonce für einen frischen Keystream.
Das Ausgabeformat ist ``v1:<base64url(nonce || ciphertext || tag)>``.

Die öffentliche Schnittstelle (``encrypt``/``decrypt``) ist so schmal, dass ein
späterer Wechsel auf ``cryptography.Fernet`` ein reiner Innentausch bliebe.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from app.config import settings

_VERSION = "v1"
_NONCE_LEN = 16
_TAG_LEN = 16


class TokenDecryptError(Exception):
    """Token ließ sich nicht entschlüsseln (Schlüsselwechsel oder Manipulation)."""


def _derive(label: bytes) -> bytes:
    """Leitet einen 32-Byte-Teilschlüssel aus dem konfigurierten Secret ab."""
    return hmac.new(
        settings.encryption_key.encode("utf-8"), label, hashlib.sha256
    ).digest()


def _keystream(k_enc: bytes, nonce: bytes, length: int) -> bytes:
    """CTR-artiger Keystream: HMAC-Blöcke über ``nonce || counter``."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            k_enc, nonce + counter.to_bytes(8, "big"), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str) -> str:
    """Verschleiert einen Token-String und gibt einen transportierbaren String zurück."""
    k_enc = _derive(b"ftc-llm-token-enc")
    k_mac = _derive(b"ftc-llm-token-mac")
    nonce = secrets.token_bytes(_NONCE_LEN)
    data = plaintext.encode("utf-8")
    ct = bytes(b ^ s for b, s in zip(data, _keystream(k_enc, nonce, len(data))))
    tag = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()[:_TAG_LEN]
    blob = base64.urlsafe_b64encode(nonce + ct + tag).decode("ascii")
    return f"{_VERSION}:{blob}"


def decrypt(token: str) -> str:
    """Kehrt ``encrypt`` um. Wirft ``TokenDecryptError`` bei ungültiger Eingabe."""
    try:
        version, _, blob = token.partition(":")
        if version != _VERSION or not blob:
            raise TokenDecryptError("Unbekanntes Token-Format")
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
    except (ValueError, TokenDecryptError) as exc:
        raise TokenDecryptError("Token konnte nicht dekodiert werden") from exc

    if len(raw) < _NONCE_LEN + _TAG_LEN:
        raise TokenDecryptError("Token zu kurz")
    nonce = raw[:_NONCE_LEN]
    tag = raw[-_TAG_LEN:]
    ct = raw[_NONCE_LEN:-_TAG_LEN]

    k_mac = _derive(b"ftc-llm-token-mac")
    expected = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()[:_TAG_LEN]
    if not hmac.compare_digest(tag, expected):
        raise TokenDecryptError("Integritätsprüfung fehlgeschlagen")

    k_enc = _derive(b"ftc-llm-token-enc")
    data = bytes(b ^ s for b, s in zip(ct, _keystream(k_enc, nonce, len(ct))))
    return data.decode("utf-8")


def encrypt_optional(plaintext: str | None) -> str | None:
    """Wie ``encrypt``, gibt bei leerem/None-Input None zurück (nichts zu speichern)."""
    if not plaintext:
        return None
    return encrypt(plaintext)


def decrypt_optional(token: str | None) -> str | None:
    """Wie ``decrypt``, toleriert None/leer und gibt dann None zurück."""
    if not token:
        return None
    return decrypt(token)
