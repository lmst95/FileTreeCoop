"""FastAPI-App: Router-Registrierung, Static/Templates, Seiten und Startup."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_optional_user
from app.db import get_db, init_db
from app.models import User
from app.routers import (
    activity,
    annotations,
    auth,
    backup,
    entries,
    export,
    llm,
    search,
    sources,
    storage,
)

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="filetree_coop", version="0.1.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(entries.router)
app.include_router(search.router)
app.include_router(annotations.router)
app.include_router(activity.router)
app.include_router(export.router)
app.include_router(llm.router)
app.include_router(storage.router)
app.include_router(backup.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user}
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is not None:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "search.html", {"request": request, "user": user}
    )


@app.get("/browse", response_class=HTMLResponse)
def browse_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "browse.html", {"request": request, "user": user}
    )


@app.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "notes.html", {"request": request, "user": user}
    )


@app.get("/overview", response_class=HTMLResponse)
def overview_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "overview.html", {"request": request, "user": user}
    )


@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "activity.html", {"request": request, "user": user}
    )


@app.get("/handovers", response_class=HTMLResponse)
def handovers_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "handovers.html", {"request": request, "user": user}
    )


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "calendar.html", {"request": request, "user": user}
    )


@app.get("/storage", response_class=HTMLResponse)
def storage_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "storage.html", {"request": request, "user": user}
    )


@app.get("/llm", response_class=HTMLResponse)
def llm_page(request: Request, user: User | None = Depends(get_optional_user)):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "llm.html", {"request": request, "user": user}
    )


@app.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": user,
            # Backup-Karte nur für das Betreiber-Konto einblenden.
            "can_backup": backup.is_backup_admin(db, user),
            "db_size": backup.database_size_human(),
        },
    )
