"""Desktop-Clients: Registrierung, Heartbeat und Befehls-Queue.

Der Desktop-Client läuft im Hintergrund auf dem Rechner des Nutzers, überwacht
konfigurierte Ordner und hält den Index aktuell. Für das eigentliche Melden von
Dateien nutzt er dieselben Endpunkte wie der Browser-Scanner
(``/api/sources/{id}/ingest``, ``/hash-todo``, ``/hashes``) – er authentifiziert
sich lediglich mit einem Gerätetoken statt mit einem Session-Cookie (siehe
``app.auth``). Hier liegt nur, was *zusätzlich* nötig ist:

- **Registrierung** – einmalig Konto-Daten gegen einen Gerätetoken tauschen.
- **Heartbeat** – „ich lebe“, aktuelle Ordner-Konfiguration melden, offene
  Befehle abholen.
- **Befehle** – z. B. „öffne diesen Ordner im Explorer“. Bewusst als Queue, die
  der Client pollt, und nicht als Push: so muss der Rechner von außen nicht
  erreichbar sein und es funktioniert hinter NAT, Proxy und Firewall.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import (
    authenticate,
    get_current_client,
    get_session_user,
    hash_client_token,
    new_client_token,
)
from app.db import get_db
from app.models import (
    Client,
    ClientCommand,
    ClientFolder,
    Entry,
    Source,
    SourceShare,
    User,
    utcnow,
)
from app.schemas import (
    ClientCommandAckIn,
    ClientCommandOut,
    ClientFolderOut,
    ClientHeartbeatIn,
    ClientHeartbeatOut,
    ClientOut,
    ClientPatchIn,
    ClientRegisterIn,
    ClientRegisterOut,
    OpenFolderIn,
    OpenFolderOut,
    UserOut,
)

router = APIRouter(prefix="/api/clients", tags=["clients"])

# Takt, in dem der Client sich meldet. Er bestimmt zugleich, wie schnell ein
# „Ordner öffnen“ ankommt – ein paar Sekunden fühlen sich wie sofort an, ohne
# den Server mit Polling zu belasten.
HEARTBEAT_SECONDS = 5
# Ab wann ein Client als offline gilt. Großzügig gegenüber einem verpassten
# Heartbeat (kurzer Netzhänger), aber kurz genug, dass die Anzeige ehrlich ist.
ONLINE_GRACE = timedelta(seconds=HEARTBEAT_SECONDS * 6)
# Ein Befehl, den niemand abgeholt hat, verfällt. Sonst öffnete ein Rechner, der
# eine Nacht aus war, beim Hochfahren auf einen Schlag zwanzig Explorer-Fenster.
COMMAND_TTL = timedelta(minutes=5)


def _naive_utc(value: datetime) -> datetime:
    """Zeitstempel ohne Zeitzone, immer in UTC.

    ``utcnow()`` liefert einen zonenbewussten Wert, SQLite gibt gespeicherte
    Zeitstempel dagegen zonenlos zurück – direkt voneinander abziehen lassen sie
    sich nicht. Verglichen wird deshalb konsequent zonenlos in UTC.
    """
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def is_online(client: Client, now=None) -> bool:
    if client.last_seen_at is None:
        return False
    reference = _naive_utc(now or utcnow())
    return reference - _naive_utc(client.last_seen_at) <= ONLINE_GRACE


def _owned_client(db: Session, user: User, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Client nicht gefunden")
    return client


def _folder_outs(db: Session, client: Client) -> list[ClientFolderOut]:
    """Ordner eines Clients samt Quellen-Bezeichnung (eine Abfrage)."""
    folders = db.scalars(
        select(ClientFolder).where(ClientFolder.client_id == client.id)
    ).all()
    if not folders:
        return []
    sources = {
        s.id: s
        for s in db.scalars(
            select(Source).where(Source.id.in_({f.source_id for f in folders}))
        ).all()
    }
    outs = []
    for f in folders:
        src = sources.get(f.source_id)
        outs.append(
            ClientFolderOut(
                source_id=f.source_id,
                local_path=f.local_path,
                enabled=f.enabled,
                hash_enabled=f.hash_enabled,
                watch_enabled=f.watch_enabled,
                scan_interval_minutes=f.scan_interval_minutes,
                last_scan_at=f.last_scan_at,
                last_error=f.last_error,
                source_label=src.label if src else "(gelöschte Quelle)",
                source_kind=src.kind if src else "",
            )
        )
    outs.sort(key=lambda f: f.source_label.lower())
    return outs


def _client_out(db: Session, client: Client, now=None) -> ClientOut:
    return ClientOut(
        id=client.id,
        name=client.name,
        hostname=client.hostname,
        platform=client.platform,
        version=client.version,
        status_text=client.status_text,
        paused=client.paused,
        created_at=client.created_at,
        last_seen_at=client.last_seen_at,
        online=is_online(client, now),
        folders=_folder_outs(db, client),
    )


# --- Registrierung ----------------------------------------------------------

@router.post("/register", response_model=ClientRegisterOut, status_code=201)
def register_client(data: ClientRegisterIn, db: Session = Depends(get_db)):
    """Einmalig Konto-Daten gegen einen Gerätetoken tauschen.

    Danach braucht der Client das Passwort nie wieder – auf der Platte liegt nur
    der Token, und der lässt sich in der Weboberfläche einzeln widerrufen, ohne
    dass das Konto-Passwort geändert werden müsste.

    Meldet sich derselbe Rechner mit demselben Namen erneut an (Neuinstallation,
    Token verloren), wird sein Eintrag samt Token *erneuert* statt ein zweiter
    angelegt – sonst sammelte die Geräteliste bei jedem Setup eine Karteileiche.
    """
    user = authenticate(db, data.identifier, data.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Anmeldedaten falsch")

    name = (data.name or data.hostname or "Desktop-Client").strip()[:120]
    client = db.scalar(
        select(Client).where(
            Client.owner_user_id == user.id,
            Client.hostname == data.hostname,
            Client.name == name,
        )
    )
    if client is None:
        client = Client(owner_user_id=user.id, name=name, token_hash="")
        db.add(client)

    token = new_client_token()
    client.token_hash = hash_client_token(token)
    client.hostname = data.hostname
    client.platform = data.platform
    client.version = data.version
    client.last_seen_at = utcnow()
    db.commit()
    db.refresh(client)
    return ClientRegisterOut(
        client_id=client.id,
        token=token,
        name=client.name,
        user=UserOut.model_validate(user),
    )


# --- Heartbeat + Befehle (Client-Seite, Auth per Gerätetoken) ---------------

def _sync_folders(db: Session, client: Client, folders) -> None:
    """Gemeldete Ordner-Konfiguration übernehmen (der Client ist die Quelle).

    Ordner zu Quellen, die dem Nutzer nicht gehören, werden ignoriert: der
    Client soll nur den Index von Quellen füttern, für die sein Besitzer
    verantwortlich ist.
    """
    owned = set(
        db.scalars(
            select(Source.id).where(Source.owner_user_id == client.owner_user_id)
        ).all()
    )
    existing = {
        f.source_id: f
        for f in db.scalars(
            select(ClientFolder).where(ClientFolder.client_id == client.id)
        ).all()
    }
    seen: set[int] = set()
    for item in folders:
        if item.source_id not in owned:
            continue
        seen.add(item.source_id)
        folder = existing.get(item.source_id)
        if folder is None:
            folder = ClientFolder(client_id=client.id, source_id=item.source_id)
            db.add(folder)
        folder.local_path = item.local_path
        folder.enabled = item.enabled
        folder.hash_enabled = item.hash_enabled
        folder.watch_enabled = item.watch_enabled
        folder.scan_interval_minutes = item.scan_interval_minutes
        folder.last_scan_at = item.last_scan_at
        folder.last_error = (item.last_error or "")[:300]
    gone = [f.id for sid, f in existing.items() if sid not in seen]
    if gone:
        db.execute(delete(ClientFolder).where(ClientFolder.id.in_(gone)))


@router.post("/heartbeat", response_model=ClientHeartbeatOut)
def heartbeat(
    data: ClientHeartbeatIn,
    client: Client = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """„Ich lebe“ – aktualisiert den Zustand und liefert offene Befehle aus."""
    now = utcnow()
    client.last_seen_at = now
    if data.version:
        client.version = data.version
    if data.name:
        client.name = data.name
    if data.hostname:
        client.hostname = data.hostname
    client.status_text = data.status_text or ""
    if data.folders is not None:
        _sync_folders(db, client, data.folders)

    # Verfallene Befehle wegräumen, bevor ausgeliefert wird.
    db.execute(
        delete(ClientCommand).where(
            ClientCommand.client_id == client.id,
            ClientCommand.status == "pending",
            ClientCommand.created_at < now - COMMAND_TTL,
        )
    )
    pending = db.scalars(
        select(ClientCommand)
        .where(ClientCommand.client_id == client.id, ClientCommand.status == "pending")
        .order_by(ClientCommand.id)
    ).all()
    out = []
    for cmd in pending:
        cmd.status = "delivered"
        cmd.delivered_at = now
        try:
            payload = json.loads(cmd.payload_json) if cmd.payload_json else {}
        except ValueError:
            payload = {}
        out.append(ClientCommandOut(id=cmd.id, command=cmd.command, payload=payload))

    db.commit()
    return ClientHeartbeatOut(
        paused=client.paused, server_time=now, commands=out
    )


@router.post("/commands/{command_id}/ack", status_code=204)
def ack_command(
    command_id: int,
    data: ClientCommandAckIn,
    client: Client = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Quittung des Clients: Befehl erledigt oder fehlgeschlagen."""
    cmd = db.get(ClientCommand, command_id)
    if cmd is None or cmd.client_id != client.id:
        raise HTTPException(status_code=404, detail="Befehl nicht gefunden")
    cmd.status = "error" if data.status == "error" else "done"
    cmd.result = (data.result or "")[:500]
    cmd.finished_at = utcnow()
    db.commit()


@router.post("/unregister", status_code=204)
def unregister(
    client: Client = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Der Client meldet sich selbst ab (Deinstallation)."""
    db.delete(client)
    db.commit()


# --- Verwaltung (Browser-Seite) ---------------------------------------------

@router.get("", response_model=list[ClientOut])
def list_clients(
    user: User = Depends(get_session_user), db: Session = Depends(get_db)
):
    """Alle Geräte des Nutzers – Grundlage der Seite „Geräte“."""
    now = utcnow()
    clients = db.scalars(
        select(Client)
        .where(Client.owner_user_id == user.id)
        .order_by(Client.name, Client.id)
    ).all()
    return [_client_out(db, c, now) for c in clients]


@router.patch("/{client_id}", response_model=ClientOut)
def patch_client(
    client_id: int,
    data: ClientPatchIn,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    """Gerät umbenennen oder aus der Ferne pausieren/fortsetzen."""
    client = _owned_client(db, user, client_id)
    if data.name is not None:
        client.name = data.name.strip()[:120]
    if data.paused is not None:
        client.paused = data.paused
    db.commit()
    db.refresh(client)
    return _client_out(db, client)


@router.delete("/{client_id}", status_code=204)
def delete_client(
    client_id: int,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    """Gerät entfernen – widerruft seinen Token sofort.

    Der Index bleibt unangetastet: Was der Client gemeldet hat, ist weiterhin
    gültig, nur eben nicht mehr aktuell gehalten.
    """
    client = _owned_client(db, user, client_id)
    db.delete(client)
    db.commit()


# --- „Ordner öffnen“ ---------------------------------------------------------

def _open_candidates(
    db: Session, user: User, source_id: int
) -> list[tuple[ClientFolder, Client]]:
    """Eigene, aktive Client-Ordner, die diese Quelle abdecken."""
    rows = db.execute(
        select(ClientFolder, Client)
        .join(Client, Client.id == ClientFolder.client_id)
        .where(
            Client.owner_user_id == user.id,
            ClientFolder.source_id == source_id,
            ClientFolder.enabled.is_(True),
            ClientFolder.local_path != "",
        )
    ).all()
    now = utcnow()
    # Online zuerst – ein Befehl an ein schlafendes Gerät verfällt ungesehen.
    return sorted(rows, key=lambda r: not is_online(r[1], now))


@router.get("/reachable-sources", response_model=list[int])
def reachable_sources(
    user: User = Depends(get_session_user), db: Session = Depends(get_db)
):
    """Quellen, für die gerade ein Client bereitsteht (blendet 📂 im UI ein)."""
    now = utcnow()
    rows = db.execute(
        select(ClientFolder.source_id, Client)
        .join(Client, Client.id == ClientFolder.client_id)
        .where(
            Client.owner_user_id == user.id,
            ClientFolder.enabled.is_(True),
            ClientFolder.local_path != "",
        )
    ).all()
    return sorted({sid for sid, client in rows if is_online(client, now)})


@router.post("/open", response_model=OpenFolderOut)
def open_folder(
    data: OpenFolderIn,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    """Einen Ordner (oder den Ordner einer Datei) auf dem Rechner öffnen.

    Der Browser darf keinen Dateimanager starten – deshalb erledigt das der
    Desktop-Client: hier landet nur ein Auftrag in seiner Queue, den er beim
    nächsten Heartbeat abholt. Übertragen wird der Pfad *relativ* zur Quelle;
    wo die Wurzel auf dem Rechner liegt, weiß allein der Client.
    """
    source = db.get(Source, data.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Quelle nicht gefunden")
    # Lesenden Zugriff verlangen – der Auftrag geht ohnehin nur an eigene Geräte.
    if source.owner_user_id != user.id:
        share = db.scalar(
            select(SourceShare).where(
                SourceShare.source_id == source.id, SourceShare.user_id == user.id
            )
        )
        if share is None:
            raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Quelle")

    path = (data.path or "").strip("/")
    if path and not db.scalar(
        select(Entry.id).where(Entry.source_id == source.id, Entry.path == path)
    ):
        raise HTTPException(status_code=404, detail="Eintrag in dieser Quelle nicht gefunden")

    candidates = _open_candidates(db, user, source.id)
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="Kein Desktop-Client überwacht diese Quelle. "
            "Unter „Geräte“ steht, wie du einen einrichtest.",
        )
    if data.client_id is not None:
        candidates = [r for r in candidates if r[1].id == data.client_id]
        if not candidates:
            raise HTTPException(status_code=404, detail="Dieser Client kennt die Quelle nicht")
    folder, client = candidates[0]
    if not is_online(client):
        raise HTTPException(
            status_code=409,
            detail=f"„{client.name}“ ist gerade offline – der Ordner kann nicht "
            "geöffnet werden.",
        )

    cmd = ClientCommand(
        client_id=client.id,
        command="open_folder",
        payload_json=json.dumps(
            {"source_id": source.id, "path": path, "is_dir": data.is_dir}
        ),
        created_by_user_id=user.id,
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)
    return OpenFolderOut(command_id=cmd.id, client_id=client.id, client_name=client.name)


@router.get("/commands/{command_id}")
def command_status(
    command_id: int,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    """Zustand eines abgesetzten Befehls – das UI wartet kurz darauf."""
    cmd = db.get(ClientCommand, command_id)
    if cmd is None:
        raise HTTPException(status_code=404, detail="Befehl nicht gefunden")
    _owned_client(db, user, cmd.client_id)
    return {"id": cmd.id, "status": cmd.status, "result": cmd.result}
