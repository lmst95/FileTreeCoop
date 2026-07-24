"""Auth-Endpunkte: Registrierung, Login, Logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import (
    authenticate,
    get_current_user,
    hash_password,
    make_session_token,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.llm.defaults import seed_default_prompts
from app.models import Invite, Source, SourceShare, User
from app.schemas import (
    LoginIn,
    MyShareOut,
    PasswordChangeIn,
    ProfileUpdateIn,
    RegisterIn,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=settings.session_cookie,
        value=make_session_token(user_id),
        httponly=True,
        samesite="lax",
        secure=settings.session_https_only,
        max_age=60 * 60 * 24 * 14,  # 14 Tage
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, response: Response, db: Session = Depends(get_db)):
    username = data.username.lower()
    if db.scalar(select(User).where(func.lower(User.email) == data.email.lower())):
        raise HTTPException(status_code=409, detail="E-Mail bereits registriert")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=409, detail="Username bereits vergeben")
    user = User(
        email=data.email,
        username=username,
        password_hash=hash_password(data.password),
        display_name=data.display_name,
    )
    db.add(user)
    db.flush()
    _redeem_invites(db, user)
    seed_default_prompts(db, user)  # Standard-Prompts als Starthilfe
    db.commit()
    db.refresh(user)
    _set_session_cookie(response, user.id)
    return user


def _redeem_invites(db: Session, user: User) -> None:
    """Ausstehende Einladungen an diese E-Mail in echte Freigaben umwandeln."""
    invites = db.scalars(
        select(Invite).where(Invite.email == user.email.lower())
    ).all()
    for inv in invites:
        exists = db.scalar(
            select(SourceShare).where(
                SourceShare.source_id == inv.source_id,
                SourceShare.user_id == user.id,
                SourceShare.path_prefix == inv.path_prefix,
            )
        )
        if exists is None:
            db.add(
                SourceShare(
                    source_id=inv.source_id,
                    user_id=user.id,
                    path_prefix=inv.path_prefix,
                    permission=inv.permission,
                )
            )
        db.delete(inv)


@router.post("/login", response_model=UserOut)
def login(data: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = authenticate(db, data.identifier, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Anmeldedaten falsch")
    _set_session_cookie(response, user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    response.delete_cookie(settings.session_cookie, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(
    data: ProfileUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    username = data.username.lower()
    email_taken = db.scalar(
        select(User).where(
            func.lower(User.email) == data.email.lower(), User.id != user.id
        )
    )
    if email_taken:
        raise HTTPException(status_code=409, detail="E-Mail bereits registriert")
    username_taken = db.scalar(
        select(User).where(User.username == username, User.id != user.id)
    )
    if username_taken:
        raise HTTPException(status_code=409, detail="Username bereits vergeben")
    user.display_name = data.display_name
    user.username = username
    user.email = data.email
    db.commit()
    db.refresh(user)
    return user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    data: PasswordChangeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Aktuelles Passwort falsch")
    user.password_hash = hash_password(data.new_password)
    db.commit()


@router.get("/me/shares", response_model=list[MyShareOut])
def my_shares(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Alles, was der Nutzer über all seine Quellen hinweg geteilt hat –
    für die Übersicht auf der Profilseite."""
    source_map = {
        s.id: s
        for s in db.scalars(
            select(Source).where(Source.owner_user_id == user.id)
        ).all()
    }
    if not source_map:
        return []

    shares = db.scalars(
        select(SourceShare).where(SourceShare.source_id.in_(source_map))
    ).all()
    users = {
        u.id: u
        for u in db.scalars(
            select(User).where(User.id.in_({s.user_id for s in shares}))
        ).all()
    }
    result = [
        MyShareOut(
            user_id=s.user_id,
            email=users[s.user_id].email,
            username=users[s.user_id].username,
            display_name=users[s.user_id].display_name,
            permission=s.permission,
            path_prefix=s.path_prefix,
            source_id=s.source_id,
            source_label=source_map[s.source_id].label,
        )
        for s in shares
        if s.user_id in users
    ]
    for inv in db.scalars(
        select(Invite).where(Invite.source_id.in_(source_map))
    ).all():
        result.append(
            MyShareOut(
                email=inv.email,
                display_name=inv.email,
                permission=inv.permission,
                path_prefix=inv.path_prefix,
                pending=True,
                invite_id=inv.id,
                source_id=inv.source_id,
                source_label=source_map[inv.source_id].label,
            )
        )
    return result
