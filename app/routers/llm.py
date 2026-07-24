"""LLM-Integration: Verbindungen, Settings, Prompts, Feature-Zuordnung und der
generische Ausführungs-Endpunkt (``/run``).

Alles ist nutzerbezogen (``owner_user_id == user.id``) und feature-unabhängig –
Notizen sind nur der erste Konsument von ``/run``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.llm import crypto, service
from app.llm.defaults import seed_default_prompts, seed_web_search_setting
from app.llm.features import FEATURES, is_valid_feature
from app.llm.jsonutil import json_dump, json_obj
from app.llm.providers import PROVIDER_TYPES, LLMError
from app.models import (
    AIRun,
    LLMConnection,
    LLMFeatureLink,
    LLMPrompt,
    LLMSetting,
    User,
)
from app.schemas import (
    AIRunOut,
    LLMConnectionIn,
    LLMConnectionOut,
    LLMConnectionPatch,
    LLMConnectionTestOut,
    LLMFeatureOptionOut,
    LLMModelsOut,
    LLMPromptIn,
    LLMPromptOut,
    LLMPromptPatch,
    LLMRunIn,
    LLMRunOut,
    LLMSettingIn,
    LLMSettingOut,
    LLMSettingPatch,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])


# --- Helfer ------------------------------------------------------------------

def _owned(db: Session, user: User, model, obj_id: int):
    """Lädt eine Zeile und stellt sicher, dass sie dem Nutzer gehört."""
    row = db.get(model, obj_id)
    if row is None or row.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail=f"{model.__name__} nicht gefunden")
    return row


def _validate_features(features: list[str]) -> list[str]:
    cleaned = []
    for key in features or []:
        if not is_valid_feature(key):
            raise HTTPException(status_code=422, detail=f"Unbekanntes Feature: {key}")
        if key not in cleaned:
            cleaned.append(key)
    return cleaned


def _features_for(db: Session, kind: str, ref_id: int) -> list[str]:
    return list(
        db.scalars(
            select(LLMFeatureLink.feature_key).where(
                LLMFeatureLink.kind == kind, LLMFeatureLink.ref_id == ref_id
            )
        ).all()
    )


def _feature_refs(db: Session, user: User, kind: str, feature_key: str) -> list[int]:
    """IDs der Settings/Prompts eines Nutzers, die für ein Feature freigegeben sind."""
    return list(
        db.scalars(
            select(LLMFeatureLink.ref_id).where(
                LLMFeatureLink.owner_user_id == user.id,
                LLMFeatureLink.kind == kind,
                LLMFeatureLink.feature_key == feature_key,
            )
        ).all()
    )


def _set_features(db: Session, user: User, kind: str, ref_id: int, features: list[str]) -> None:
    """Ersetzt die Feature-Zuordnungen eines Settings/Prompts."""
    db.execute(
        delete(LLMFeatureLink).where(
            LLMFeatureLink.kind == kind, LLMFeatureLink.ref_id == ref_id
        )
    )
    for key in features:
        db.add(
            LLMFeatureLink(
                owner_user_id=user.id, kind=kind, ref_id=ref_id, feature_key=key
            )
        )


# --- Serialisierung ----------------------------------------------------------

def _conn_out(conn: LLMConnection) -> LLMConnectionOut:
    hint = ""
    has_key = bool(conn.api_key_enc)
    if has_key:
        try:
            raw = crypto.decrypt(conn.api_key_enc)
            hint = "••••" + raw[-4:] if len(raw) >= 4 else "••••"
        except crypto.TokenDecryptError:
            hint = "••••"  # Token vorhanden, aber Schlüssel passt nicht mehr
    cache = json_obj(conn.models_cache_json)
    fetched = cache.get("fetched_at")
    fetched_dt = None
    if isinstance(fetched, str):
        try:
            fetched_dt = datetime.fromisoformat(fetched)
        except ValueError:
            fetched_dt = None
    return LLMConnectionOut(
        id=conn.id,
        label=conn.label,
        provider_type=conn.provider_type,
        base_url=conn.base_url,
        default_model=conn.default_model,
        extra=json_obj(conn.extra_json),
        has_key=has_key,
        key_hint=hint,
        models=cache.get("models", []) if isinstance(cache.get("models"), list) else [],
        models_fetched_at=fetched_dt,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


def _setting_out(db: Session, s: LLMSetting) -> LLMSettingOut:
    conn = db.get(LLMConnection, s.connection_id)
    return LLMSettingOut(
        id=s.id,
        label=s.label,
        connection_id=s.connection_id,
        connection_label=conn.label if conn else "",
        model=s.model,
        system_prompt=s.system_prompt,
        params=json_obj(s.params_json),
        features=_features_for(db, "setting", s.id),
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _prompt_out(db: Session, p: LLMPrompt) -> LLMPromptOut:
    return LLMPromptOut(
        id=p.id,
        name=p.name,
        body=p.body,
        description=p.description,
        features=_features_for(db, "prompt", p.id),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _airun_out(r: AIRun) -> AIRunOut:
    return AIRunOut(
        id=r.id,
        target_kind=r.target_kind,
        target_id=r.target_id,
        setting_id=r.setting_id,
        prompt_id=r.prompt_id,
        input_text=r.input_text,
        output_text=r.output_text,
        status=r.status,
        error=r.error,
        meta=json_obj(r.meta_json),
        created_at=r.created_at,
    )


# --- Metadaten (Provider-Typen, Features) ------------------------------------

@router.get("/meta")
def get_meta(user: User = Depends(get_current_user)):
    """Statische Auswahllisten fürs UI: Provider-Typen und bekannte Features."""
    return {
        "provider_types": PROVIDER_TYPES,
        "features": [{"key": k, "label": v} for k, v in FEATURES.items()],
    }


# --- Verbindungen ------------------------------------------------------------

@router.get("/connections", response_model=list[LLMConnectionOut])
def list_connections(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(LLMConnection)
        .where(LLMConnection.owner_user_id == user.id)
        .order_by(LLMConnection.label)
    ).all()
    return [_conn_out(c) for c in rows]


@router.post("/connections", response_model=LLMConnectionOut, status_code=201)
def create_connection(
    data: LLMConnectionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = LLMConnection(
        owner_user_id=user.id,
        label=data.label,
        provider_type=data.provider_type,
        base_url=data.base_url,
        api_key_enc=crypto.encrypt_optional(data.api_key),
        default_model=data.default_model,
        extra_json=json_dump(data.extra),
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return _conn_out(conn)


@router.patch("/connections/{conn_id}", response_model=LLMConnectionOut)
def update_connection(
    conn_id: int,
    patch: LLMConnectionPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = _owned(db, user, LLMConnection, conn_id)
    if patch.label is not None:
        conn.label = patch.label
    if patch.provider_type is not None:
        conn.provider_type = patch.provider_type
    if patch.base_url is not None:
        conn.base_url = patch.base_url
    if patch.default_model is not None:
        conn.default_model = patch.default_model
    if patch.extra is not None:
        conn.extra_json = json_dump(patch.extra)
    # api_key: Anwesenheit des Feldes entscheidet (""=löschen, Wert=ersetzen).
    if "api_key" in patch.model_fields_set:
        conn.api_key_enc = crypto.encrypt_optional(patch.api_key)
    db.commit()
    db.refresh(conn)
    return _conn_out(conn)


@router.delete("/connections/{conn_id}", status_code=204)
def delete_connection(
    conn_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = _owned(db, user, LLMConnection, conn_id)
    db.delete(conn)
    db.commit()


@router.post("/connections/{conn_id}/test", response_model=LLMConnectionTestOut)
def test_connection(
    conn_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Prüft die Erreichbarkeit, indem die Modell-Liste abgerufen wird."""
    conn = _owned(db, user, LLMConnection, conn_id)
    try:
        models = service.list_models(conn)
    except LLMError as exc:
        return LLMConnectionTestOut(ok=False, detail=str(exc))
    if models is None:
        return LLMConnectionTestOut(
            ok=True, detail="Verbunden (Modell-Liste wird nicht unterstützt)."
        )
    return LLMConnectionTestOut(
        ok=True, detail=f"Verbunden – {len(models)} Modelle gefunden.",
        models_count=len(models),
    )


@router.post("/connections/{conn_id}/models", response_model=LLMModelsOut)
def refresh_models(
    conn_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ruft die verfügbaren Modelle live ab und aktualisiert den Cache."""
    conn = _owned(db, user, LLMConnection, conn_id)
    try:
        models = service.list_models(conn)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if models is None:
        return LLMModelsOut(supported=False, models=[])
    conn.models_cache_json = json_dump(
        {"models": models, "fetched_at": datetime.now(timezone.utc).isoformat()}
    )
    db.commit()
    return LLMModelsOut(supported=True, models=models)


# --- Settings ----------------------------------------------------------------

@router.get("/settings", response_model=list[LLMSettingOut])
def list_settings(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(LLMSetting)
        .where(LLMSetting.owner_user_id == user.id)
        .order_by(LLMSetting.label)
    ).all()
    return [_setting_out(db, s) for s in rows]


@router.post("/settings", response_model=LLMSettingOut, status_code=201)
def create_setting(
    data: LLMSettingIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owned(db, user, LLMConnection, data.connection_id)  # muss dem Nutzer gehören
    features = _validate_features(data.features)
    s = LLMSetting(
        owner_user_id=user.id,
        connection_id=data.connection_id,
        label=data.label,
        model=data.model,
        system_prompt=data.system_prompt,
        params_json=json_dump(data.params),
    )
    db.add(s)
    db.flush()  # s.id für die Feature-Links
    _set_features(db, user, "setting", s.id, features)
    db.commit()
    db.refresh(s)
    return _setting_out(db, s)


@router.post("/settings/web-search", response_model=LLMSettingOut, status_code=201)
def create_web_search_setting(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Legt (idempotent) ein fertiges Web-Suche-Setting aus einer OpenAI-Verbindung an.

    Web-Suche über ``/chat/completions`` funktioniert nur bei OpenAI; daher wird
    die erste OpenAI-Verbindung des Nutzers herangezogen. Fehlt eine, gibt es
    einen sprechenden Hinweis (409).
    """
    conn = db.scalar(
        select(LLMConnection)
        .where(
            LLMConnection.owner_user_id == user.id,
            LLMConnection.provider_type == "openai",
        )
        .order_by(LLMConnection.label)
    )
    if conn is None:
        raise HTTPException(
            status_code=409,
            detail="Lege zuerst eine OpenAI-Verbindung an (Anbieter-Typ „OpenAI“).",
        )
    s = seed_web_search_setting(db, user, conn)
    db.commit()
    db.refresh(s)
    return _setting_out(db, s)


@router.patch("/settings/{setting_id}", response_model=LLMSettingOut)
def update_setting(
    setting_id: int,
    patch: LLMSettingPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = _owned(db, user, LLMSetting, setting_id)
    if patch.connection_id is not None:
        _owned(db, user, LLMConnection, patch.connection_id)
        s.connection_id = patch.connection_id
    if patch.label is not None:
        s.label = patch.label
    if patch.model is not None:
        s.model = patch.model
    if patch.system_prompt is not None:
        s.system_prompt = patch.system_prompt
    if patch.params is not None:
        s.params_json = json_dump(patch.params)
    if patch.features is not None:
        _set_features(db, user, "setting", s.id, _validate_features(patch.features))
    db.commit()
    db.refresh(s)
    return _setting_out(db, s)


@router.delete("/settings/{setting_id}", status_code=204)
def delete_setting(
    setting_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = _owned(db, user, LLMSetting, setting_id)
    db.execute(
        delete(LLMFeatureLink).where(
            LLMFeatureLink.kind == "setting", LLMFeatureLink.ref_id == s.id
        )
    )
    db.delete(s)
    db.commit()


# --- Prompts -----------------------------------------------------------------

@router.get("/prompts", response_model=list[LLMPromptOut])
def list_prompts(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(LLMPrompt)
        .where(LLMPrompt.owner_user_id == user.id)
        .order_by(LLMPrompt.name)
    ).all()
    return [_prompt_out(db, p) for p in rows]


@router.post("/prompts", response_model=LLMPromptOut, status_code=201)
def create_prompt(
    data: LLMPromptIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    features = _validate_features(data.features)
    p = LLMPrompt(
        owner_user_id=user.id,
        name=data.name,
        body=data.body,
        description=data.description,
    )
    db.add(p)
    db.flush()
    _set_features(db, user, "prompt", p.id, features)
    db.commit()
    db.refresh(p)
    return _prompt_out(db, p)


@router.post("/prompts/defaults", response_model=list[LLMPromptOut], status_code=201)
def create_default_prompts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Legt die Standard-Prompts an (idempotent) und gibt sie zurück.

    Neue Nutzer bekommen sie schon bei der Registrierung; dieser Endpunkt ist für
    bestehende Nutzer bzw. zum Wiederherstellen versehentlich gelöschter Vorlagen.
    """
    prompts = seed_default_prompts(db, user)
    db.commit()
    return [_prompt_out(db, p) for p in prompts]


@router.patch("/prompts/{prompt_id}", response_model=LLMPromptOut)
def update_prompt(
    prompt_id: int,
    patch: LLMPromptPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = _owned(db, user, LLMPrompt, prompt_id)
    if patch.name is not None:
        p.name = patch.name
    if patch.body is not None:
        p.body = patch.body
    if patch.description is not None:
        p.description = patch.description
    if patch.features is not None:
        _set_features(db, user, "prompt", p.id, _validate_features(patch.features))
    db.commit()
    db.refresh(p)
    return _prompt_out(db, p)


@router.delete("/prompts/{prompt_id}", status_code=204)
def delete_prompt(
    prompt_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = _owned(db, user, LLMPrompt, prompt_id)
    db.execute(
        delete(LLMFeatureLink).where(
            LLMFeatureLink.kind == "prompt", LLMFeatureLink.ref_id == p.id
        )
    )
    db.delete(p)
    db.commit()


# --- Feature-Optionen (für Dropdowns eines konkreten Features) ---------------

@router.get("/features/{feature_key}", response_model=LLMFeatureOptionOut)
def feature_options(
    feature_key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Settings + Prompts, die für dieses Feature freigegeben sind."""
    if not is_valid_feature(feature_key):
        raise HTTPException(status_code=404, detail="Unbekanntes Feature")

    setting_ids = _feature_refs(db, user, "setting", feature_key)
    prompt_ids = _feature_refs(db, user, "prompt", feature_key)

    settings = (
        db.scalars(
            select(LLMSetting)
            .where(LLMSetting.id.in_(setting_ids))
            .order_by(LLMSetting.label)
        ).all()
        if setting_ids else []
    )
    prompts = (
        db.scalars(
            select(LLMPrompt)
            .where(LLMPrompt.id.in_(prompt_ids))
            .order_by(LLMPrompt.name)
        ).all()
        if prompt_ids else []
    )
    return LLMFeatureOptionOut(
        settings=[_setting_out(db, s) for s in settings],
        prompts=[_prompt_out(db, p) for p in prompts],
    )


# --- Generischer Lauf --------------------------------------------------------

def _persist_run(
    db: Session,
    user: User,
    data: LLMRunIn,
    *,
    setting_id: int,
    prompt_id: int | None,
    meta: dict,
    output: str,
    error: str,
) -> AIRun:
    """Schreibt einen (Erfolgs- oder Fehl-)Lauf in ``ai_runs`` und committet."""
    row = AIRun(
        owner_user_id=user.id,
        target_kind=data.target_kind,
        target_id=data.target_id,
        setting_id=setting_id,
        prompt_id=prompt_id,
        input_text=data.input_text,
        output_text=output,
        status="error" if error else "ok",
        error=error,
        meta_json=json_dump(meta),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/run", response_model=LLMRunOut)
def run(
    data: LLMRunIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Führt eine Completion aus – der eine Endpunkt für alle Features.

    Bindet den Lauf optional an ein Objekt (``target_kind``/``target_id``) und
    hält ihn im Verlauf fest. Das eigentliche Zurückschreiben eines Ergebnisses
    in ein Feature-Objekt (z. B. eine Notiz) läuft über dessen eigene API und
    deren Zugriffsprüfung – hier wird nur erzeugt und protokolliert.
    """
    setting = _owned(db, user, LLMSetting, data.setting_id)
    conn = _owned(db, user, LLMConnection, setting.connection_id)

    prompt_body = ""
    prompt_id = None
    prompt_name = ""
    if data.prompt_id is not None:
        prompt = _owned(db, user, LLMPrompt, data.prompt_id)
        prompt_body = prompt.body
        prompt_id = prompt.id
        prompt_name = prompt.name
    elif data.prompt_text is not None:
        prompt_body = data.prompt_text

    meta = {
        "model": setting.model,
        "setting_label": setting.label,
        "connection_label": conn.label,
        "prompt_name": prompt_name,
    }

    try:
        output = service.run_completion(
            connection=conn,
            model=setting.model,
            system_prompt=setting.system_prompt,
            params=json_obj(setting.params_json),
            prompt_body=prompt_body,
            input_text=data.input_text,
        )
    except LLMError as exc:
        if data.persist:
            _persist_run(
                db, user, data, setting_id=setting.id, prompt_id=prompt_id,
                meta=meta, output="", error=str(exc),
            )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run_id = None
    created = None
    if data.persist:
        row = _persist_run(
            db, user, data, setting_id=setting.id, prompt_id=prompt_id,
            meta=meta, output=output, error="",
        )
        run_id = row.id
        created = row.created_at

    return LLMRunOut(
        run_id=run_id, output_text=output, status="ok",
        model=setting.model, created_at=created,
    )


@router.get("/runs", response_model=list[AIRunOut])
def list_runs(
    target_kind: str | None = Query(default=None),
    target_id: int | None = Query(default=None),
    limit: int = Query(default=20, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lauf-Verlauf des Nutzers, optional auf ein Ziel gefiltert (neueste zuerst)."""
    stmt = select(AIRun).where(AIRun.owner_user_id == user.id)
    if target_kind is not None:
        stmt = stmt.where(AIRun.target_kind == target_kind)
    if target_id is not None:
        stmt = stmt.where(AIRun.target_id == target_id)
    stmt = stmt.order_by(AIRun.created_at.desc()).limit(limit)
    return [_airun_out(r) for r in db.scalars(stmt).all()]
