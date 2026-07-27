"""Speicherplatz-Ansicht: wo liegt das Volumen, was ist alt, was liegt doppelt?

Alle Auswertungen laufen über den vorhandenen Index (``entries``) und sind
scope-genau gefiltert – wer nur einen Teilbaum freigegeben bekam, sieht auch
nur dessen Zahlen. Gerechnet wird ausschließlich über *vorhandene* Dateien;
Verschwundenes belegt keinen Platz mehr und wird nur in der Summe ausgewiesen.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from app.access import (
    accessible_scopes,
    like_escape,
    path_in_scope,
    scope_entry_condition,
    scopes_for_source,
)
from app.auth import get_current_user
from app.db import get_db
from app.models import Entry, Source, User
from app.schemas import (
    AgeBucketOut,
    DuplicateGroupOut,
    DuplicateMemberOut,
    FolderChildOut,
    FolderLevelOut,
    StorageEntryOut,
    StorageSummaryOut,
    StorageSourceOut,
    TypeStatOut,
)

router = APIRouter(prefix="/api/storage", tags=["storage"])

# Altersklassen nach Änderungsdatum (Tage, aufsteigend; None = alles darüber).
AGE_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("letzte 30 Tage", 30),
    ("bis 1 Jahr", 365),
    ("1–2 Jahre", 730),
    ("2–5 Jahre", 1825),
    ("älter als 5 Jahre", None),
)


def _file_conditions(scopes, *, source_id: int | None):
    """Basisfilter aller Auswertungen: zugängliche, vorhandene Dateien."""
    conds = [
        scope_entry_condition(scopes),
        Entry.is_dir.is_(False),
        Entry.status == "present",
    ]
    if source_id is not None:
        conds.append(Entry.source_id == source_id)
    return conds


def _scopes_or_404(db: Session, user: User, source_id: int | None):
    """Zugängliche Scopes – bei gesetzter Quelle auf diese eingeschränkt."""
    scopes = accessible_scopes(db, user)
    if source_id is None:
        return scopes
    only = scopes_for_source(scopes, source_id)
    if not only:
        raise HTTPException(status_code=404, detail="Quelle nicht gefunden")
    return only


def _source_labels(db: Session, source_ids) -> dict[int, str]:
    if not source_ids:
        return {}
    return {
        s.id: s.label
        for s in db.scalars(select(Source).where(Source.id.in_(set(source_ids)))).all()
    }


def _entry_out(e: Entry, labels: dict[int, str]) -> StorageEntryOut:
    return StorageEntryOut(
        entry_id=e.id,
        source_id=e.source_id,
        source_label=labels.get(e.source_id, "?"),
        path=e.path,
        name=e.name,
        ext=e.ext,
        size=e.size,
        mtime=e.mtime,
    )


@router.get("/summary", response_model=StorageSummaryOut)
def summary(
    source_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Kennzahlen gesamt und je Quelle (Grundlage der Kachelreihe)."""
    scopes = _scopes_or_404(db, user, source_id)
    conds = _file_conditions(scopes, source_id=source_id)

    total_size, files = db.execute(
        select(func.coalesce(func.sum(Entry.size), 0), func.count(Entry.id)).where(*conds)
    ).one()

    dirs = db.scalar(
        select(func.count(Entry.id)).where(
            scope_entry_condition(scopes),
            Entry.is_dir.is_(True),
            Entry.status == "present",
            *([Entry.source_id == source_id] if source_id is not None else []),
        )
    ) or 0

    missing, missing_size = db.execute(
        select(func.count(Entry.id), func.coalesce(func.sum(Entry.size), 0)).where(
            scope_entry_condition(scopes),
            Entry.is_dir.is_(False),
            Entry.status == "missing",
            *([Entry.source_id == source_id] if source_id is not None else []),
        )
    ).one()

    # Je Quelle aufschlüsseln – auch als Auswahlliste für das UI brauchbar.
    rows = db.execute(
        select(
            Entry.source_id,
            func.coalesce(func.sum(Entry.size), 0),
            func.count(Entry.id),
        )
        .where(*conds)
        .group_by(Entry.source_id)
        .order_by(func.sum(Entry.size).desc())
    ).all()
    labels = _source_labels(db, [r[0] for r in rows])

    return StorageSummaryOut(
        total_size=int(total_size),
        files=int(files),
        dirs=int(dirs),
        missing=int(missing),
        missing_size=int(missing_size),
        sources=[
            StorageSourceOut(
                source_id=sid,
                label=labels.get(sid, "?"),
                size=int(size),
                files=int(cnt),
            )
            for sid, size, cnt in rows
        ],
    )


@router.get("/folders", response_model=FolderLevelOut)
def folders(
    source_id: int,
    parent: str = Query(default="", description="Ordnerpfad; leer = Wurzel"),
    limit: int = Query(default=300, le=2000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Eine Ebene des Baums mit **rekursiver** Größe je direktem Unterordner.

    Die Aggregation läuft in einer einzigen Query: der Pfad wird relativ zum
    Elternordner auf sein erstes Segment gekürzt und darüber gruppiert – damit
    kostet ein Drilldown eine Abfrage statt einer pro Ordner.
    """
    scopes = _scopes_or_404(db, user, source_id)
    parent = parent.strip("/")
    if parent and not any(path_in_scope(parent, s.path_prefix) for s in scopes):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Ordner")

    conds = _file_conditions(scopes, source_id=source_id)
    if parent:
        # Alles unterhalb des Elternordners, relativ zu ihm betrachtet.
        conds.append(Entry.path.like(f"{like_escape(parent)}/%", escape="\\"))
        rel = func.substr(Entry.path, len(parent) + 2)
    else:
        rel = Entry.path

    sep = func.instr(rel, "/")
    first_segment = case((sep > 0, func.substr(rel, 1, sep - 1)), else_=rel)

    rows = db.execute(
        select(
            first_segment.label("child"),
            func.coalesce(func.sum(Entry.size), 0).label("size"),
            func.count(Entry.id).label("files"),
        )
        .where(*conds)
        .group_by(first_segment)
        .order_by(func.sum(Entry.size).desc())
        .limit(limit)
    ).all()
    agg = {r[0]: (int(r[1]), int(r[2])) for r in rows}

    # Ob ein Kind Ordner oder Datei ist, steht im Index – die Aggregation kennt
    # nur Namen. Direkte Kinder deshalb separat holen.
    child_paths = [f"{parent}/{name}" if parent else name for name in agg]
    kinds: dict[str, bool] = {}
    if child_paths:
        for e in db.scalars(
            select(Entry).where(Entry.source_id == source_id, Entry.path.in_(child_paths))
        ).all():
            kinds[e.name] = e.is_dir

    total = sum(size for size, _ in agg.values())
    children = [
        FolderChildOut(
            name=name,
            path=f"{parent}/{name}" if parent else name,
            # Fehlt der direkte Eintrag (z. B. nach einem Teil-Cleanup), gilt
            # als Ordner, was mehr als eine Datei unter sich hat.
            is_dir=kinds.get(name, files > 1),
            size=size,
            files=files,
        )
        for name, (size, files) in agg.items()
    ]
    children.sort(key=lambda c: c.size, reverse=True)
    return FolderLevelOut(parent=parent, total_size=total, children=children)


@router.get("/largest", response_model=list[StorageEntryOut])
def largest(
    source_id: int | None = None,
    limit: int = Query(default=50, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Die größten Dateien im Zugriff."""
    scopes = _scopes_or_404(db, user, source_id)
    entries = db.scalars(
        select(Entry)
        .where(*_file_conditions(scopes, source_id=source_id))
        .order_by(Entry.size.desc())
        .limit(limit)
    ).all()
    return [_entry_out(e, _source_labels(db, [e.source_id for e in entries])) for e in entries]


@router.get("/oldest", response_model=list[StorageEntryOut])
def oldest(
    source_id: int | None = None,
    days: int = Query(default=730, ge=0, description="nicht geändert seit … Tagen"),
    limit: int = Query(default=50, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Große Dateien, die lange nicht angefasst wurden – Archiv-Kandidaten."""
    import time

    scopes = _scopes_or_404(db, user, source_id)
    cutoff = time.time() - days * 86400
    entries = db.scalars(
        select(Entry)
        .where(
            *_file_conditions(scopes, source_id=source_id),
            Entry.mtime > 0,  # 0 = unbekanntes Datum, nicht als „uralt“ werten
            Entry.mtime < cutoff,
        )
        .order_by(Entry.size.desc())
        .limit(limit)
    ).all()
    return [_entry_out(e, _source_labels(db, [e.source_id for e in entries])) for e in entries]


@router.get("/types", response_model=list[TypeStatOut])
def types(
    source_id: int | None = None,
    limit: int = Query(default=12, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Volumen je Dateiendung (größte zuerst)."""
    scopes = _scopes_or_404(db, user, source_id)
    rows = db.execute(
        select(
            Entry.ext,
            func.coalesce(func.sum(Entry.size), 0),
            func.count(Entry.id),
        )
        .where(*_file_conditions(scopes, source_id=source_id))
        .group_by(Entry.ext)
        .order_by(func.sum(Entry.size).desc())
        .limit(limit)
    ).all()
    return [
        TypeStatOut(ext=ext or "(ohne)", size=int(size), files=int(cnt))
        for ext, size, cnt in rows
    ]


@router.get("/ages", response_model=list[AgeBucketOut])
def ages(
    source_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Volumen nach Alter (Änderungsdatum) in festen Klassen."""
    import time

    scopes = _scopes_or_404(db, user, source_id)
    now = time.time()
    conds = _file_conditions(scopes, source_id=source_id)

    out: list[AgeBucketOut] = []
    lower: int | None = 0
    for label, days in AGE_BUCKETS:
        bucket = list(conds)
        if days is not None:
            bucket.append(Entry.mtime >= now - days * 86400)
        if lower:
            bucket.append(Entry.mtime < now - lower * 86400)
        # Dateien ohne Datum (mtime == 0) landen nirgends – sonst wären sie
        # per Definition „uralt“ und würden die letzte Klasse verfälschen.
        bucket.append(Entry.mtime > 0)
        size, count = db.execute(
            select(func.coalesce(func.sum(Entry.size), 0), func.count(Entry.id)).where(
                *bucket
            )
        ).one()
        out.append(
            AgeBucketOut(label=label, days=days, size=int(size), files=int(count))
        )
        lower = days
    return out


@router.get("/duplicates", response_model=list[DuplicateGroupOut])
def duplicates(
    source_id: int | None = None,
    min_size: int = Query(default=1, ge=0, description="kleinere Dateien ignorieren"),
    limit: int = Query(default=50, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dateien mit identischem Inhalt, nach verschwendetem Platz sortiert.

    Grundlage sind die im Browser berechneten SHA-256-Hashes; ohne Hash-Lauf
    ist die Liste leer. Verglichen wird über alle zugänglichen Quellen hinweg –
    dieselbe Datei auf Laptop *und* Netzlaufwerk fällt damit auf.
    """
    scopes = _scopes_or_404(db, user, source_id)
    conds = [
        *_file_conditions(scopes, source_id=source_id),
        Entry.hash_state == "ok",
        Entry.content_hash != "",
        Entry.size >= min_size,
    ]

    # Verschwendet = Größe × (Kopien − 1). Innerhalb einer Gruppe ist die Größe
    # per Definition gleich, deshalb genügt max(size).
    wasted = (func.max(Entry.size) * (func.count(Entry.id) - 1)).cast(Integer)
    rows = db.execute(
        select(
            Entry.content_hash,
            func.max(Entry.size),
            func.count(Entry.id),
            wasted.label("wasted"),
        )
        .where(*conds)
        .group_by(Entry.content_hash)
        .having(func.count(Entry.id) > 1)
        .order_by(wasted.desc())
        .limit(limit)
    ).all()
    if not rows:
        return []

    hashes = [r[0] for r in rows]
    members: dict[str, list[Entry]] = {}
    for e in db.scalars(
        select(Entry).where(*conds, Entry.content_hash.in_(hashes)).order_by(Entry.path)
    ).all():
        members.setdefault(e.content_hash, []).append(e)

    labels = _source_labels(
        db, [e.source_id for group in members.values() for e in group]
    )
    return [
        DuplicateGroupOut(
            content_hash=h,
            size=int(size),
            count=int(count),
            wasted=int(waste),
            entries=[
                DuplicateMemberOut(
                    entry_id=e.id,
                    source_id=e.source_id,
                    source_label=labels.get(e.source_id, "?"),
                    path=e.path,
                    name=e.name,
                )
                for e in members.get(h, [])
            ],
        )
        for h, size, count, waste in rows
    ]
