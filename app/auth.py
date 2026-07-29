"""Authentifizierung: Passwort-Hashing, Session-Cookies und Gerätetokens.

Zwei Wege führen zu einem Nutzer:

- **Session-Cookie** – der Browser, signiert per ``itsdangerous``.
- **Gerätetoken** – der Desktop-Client, als ``Authorization: Bearer …``.
  Er löst auf den Besitzer des Clients auf, damit der Agent dieselben
  Endpunkte (``/ingest``, ``/hash-todo`` …) nutzen kann wie der Browser-Scanner,
  ohne dass irgendwo ein Passwort dauerhaft liegen muss.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Client, User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_serializer = URLSafeSerializer(settings.secret_key, salt="ftc-session")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd.verify(password, password_hash)


def make_session_token(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_session_token(token: str) -> int | None:
    try:
        data = _serializer.loads(token)
    except BadSignature:
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, int) else None


# --- Gerätetokens (Desktop-Client) ------------------------------------------

def new_client_token() -> str:
    """Erzeugt einen neuen Gerätetoken (wird genau einmal ausgegeben)."""
    return secrets.token_urlsafe(32)


def hash_client_token(token: str) -> str:
    """Speicherform eines Gerätetokens: SHA-256 als Hex.

    Bewusst ein schneller Hash und kein bcrypt: der Token ist ein zufälliger
    256-Bit-Wert, kein ratbares Passwort – hier zählt, dass ein DB-Leck keine
    einsetzbaren Tokens preisgibt, und dass der Vergleich pro Request billig
    bleibt (der Client ruft im Sekundentakt an).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_token(request: Request) -> str | None:
    """Liest den Token aus dem ``Authorization: Bearer …``-Header."""
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def client_from_request(request: Request, db: Session) -> Client | None:
    token = bearer_token(request)
    if not token:
        return None
    return db.scalar(
        select(Client).where(Client.token_hash == hash_client_token(token))
    )


def get_current_client(
    request: Request, db: Session = Depends(get_db)
) -> Client:
    """Dependency für Client-eigene Endpunkte (Heartbeat, Befehls-Quittung)."""
    client = client_from_request(request, db)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Gerätetoken fehlt oder ist ungültig",
        )
    return client


def _current_user_or_none(request: Request, db: Session) -> User | None:
    token = request.cookies.get(settings.session_cookie)
    if token:
        uid = read_session_token(token)
        if uid is not None:
            user = db.get(User, uid)
            if user is not None:
                return user
    # Kein (gültiges) Cookie -> Gerätetoken des Desktop-Clients versuchen. Er
    # handelt stets im Namen seines Besitzers, deshalb genügt das für alle
    # Endpunkte, die ohnehin nur eigene Quellen zulassen.
    client = client_from_request(request, db)
    return db.get(User, client.owner_user_id) if client else None


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Dependency: erzwingt eine gültige Session (oder Gerätetoken), sonst 401."""
    user = _current_user_or_none(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht angemeldet"
        )
    return user


def get_session_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Dependency für Aktionen, die ein *Mensch* am Browser auslösen muss.

    Ein Gerätetoken zählt hier ausdrücklich nicht. Er liegt im Klartext auf dem
    Rechner des Nutzers und soll deshalb nur das können, wofür der Client ihn
    braucht (Index abgleichen) – nicht das Konto übernehmen. Alles, was darüber
    hinausgeht (Passwort ändern, Voll-Backup, Geräte verwalten), verlangt eine
    echte Anmeldung.
    """
    token = request.cookies.get(settings.session_cookie)
    uid = read_session_token(token) if token else None
    user = db.get(User, uid) if uid is not None else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dafür ist eine Anmeldung im Browser nötig",
        )
    return user


def get_optional_user(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """Dependency: liefert den Nutzer oder None (für Seiten ohne Login-Zwang)."""
    return _current_user_or_none(request, db)


def find_user(db: Session, identifier: str) -> User | None:
    """Findet einen Nutzer per E-Mail oder Username (jeweils case-insensitiv)."""
    ident = (identifier or "").strip().lower()
    if not ident:
        return None
    return db.scalar(
        select(User).where(
            or_(func.lower(User.email) == ident, User.username == ident)
        )
    )


def authenticate(db: Session, identifier: str, password: str) -> User | None:
    user = find_user(db, identifier)
    if user and verify_password(password, user.password_hash):
        return user
    return None
