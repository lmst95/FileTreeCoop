"""Authentifizierung: Passwort-Hashing und signierte Session-Cookies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

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


def _current_user_or_none(request: Request, db: Session) -> User | None:
    token = request.cookies.get(settings.session_cookie)
    if not token:
        return None
    uid = read_session_token(token)
    if uid is None:
        return None
    return db.get(User, uid)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Dependency: erzwingt eine gültige Session, sonst 401."""
    user = _current_user_or_none(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht angemeldet"
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
