"""Quellen-CRUD und der Ingest-Endpunkt (Batch-Upsert vom Browser-Scanner)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.access import path_in_scope
from app.auth import find_user, get_current_user
from app.db import get_db
from app.models import (
    Annotation,
    Entry,
    EntryChange,
    Invite,
    Scan,
    ScanSkip,
    Source,
    SourceShare,
    SourceVisit,
    User,
    utcnow,
)
from app.schemas import (
    EntryChangeOut,
    HashBatchIn,
    HashSummaryOut,
    HashTodoOut,
    IngestBatchIn,
    IngestResult,
    MemberOut,
    ScanOut,
    ScanSkipOut,
    ShareIn,
    ShareOut,
    SourceIn,
    SourceOut,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _accessible_source(db: Session, user: User, source_id: int) -> Source:
    """Quelle laden, falls der Nutzer Besitzer ist oder sie geteilt bekam."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Quelle nicht gefunden")
    if source.owner_user_id == user.id:
        return source
    share = db.scalar(
        select(SourceShare).where(
            SourceShare.source_id == source_id, SourceShare.user_id == user.id
        )
    )
    if share is None:
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Quelle")
    return source


def _owned_source(db: Session, user: User, source_id: int) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Quelle nicht gefunden")
    if source.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Nur der Besitzer darf das")
    return source


def _last_scans(db: Session, source_ids: list[int]) -> dict[int, Scan]:
    """Letzter abgeschlossener Voll-Scan je Quelle (Diff-Zeile im Dashboard).

    Live-Deltas des Desktop-Clients bleiben außen vor: sie beschreiben nur die
    letzte gespeicherte Datei, nicht den Zustand der Quelle – „+1 neu“ nach jedem
    Speichern wäre als Zusammenfassung irreführend.
    """
    if not source_ids:
        return {}
    result: dict[int, Scan] = {}
    for scan in db.scalars(
        select(Scan)
        .where(
            Scan.source_id.in_(source_ids),
            Scan.status == "done",
            Scan.kind == "full",
        )
        .order_by(Scan.started_at, Scan.id)
    ).all():
        result[scan.source_id] = scan  # späterer Lauf überschreibt früheren
    return result


@router.get("", response_model=list[SourceOut])
def list_sources(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Eigene Quellen plus die mit mir geteilten."""
    own = db.scalars(select(Source).where(Source.owner_user_id == user.id)).all()
    shared_ids = db.scalars(
        select(SourceShare.source_id).where(SourceShare.user_id == user.id)
    ).all()
    shared = (
        db.scalars(select(Source).where(Source.id.in_(shared_ids))).all()
        if shared_ids
        else []
    )
    sources = list(own) + list(shared)
    scans = _last_scans(db, [s.id for s in sources])
    outs = []
    for s in sources:
        out = SourceOut.model_validate(s)
        if s.id in scans:
            out.last_scan = ScanOut.model_validate(scans[s.id])
        outs.append(out)
    return outs


@router.post("", response_model=SourceOut, status_code=201)
def create_source(
    data: SourceIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = Source(
        owner_user_id=user.id,
        label=data.label,
        kind=data.kind,
        host_hint=data.host_hint,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=204)
def delete_source(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _owned_source(db, user, source_id)
    db.delete(source)
    db.commit()


# Wie viele Live-Läufe je Quelle aufgehoben werden. Die Überwachung des
# Desktop-Clients erzeugt pro Änderungs-Schub einen Lauf; ohne Deckel wüchse die
# Tabelle unbegrenzt. Die letzten paar hundert genügen für „was hat der Client
# zuletzt gemacht?“, alles darüber ist Ballast.
LIVE_SCAN_KEEP = 200


def _prune_live_scans(db: Session, source: Source) -> None:
    """Alte Live-Läufe einer Quelle wegräumen (samt ihrer entry_changes)."""
    stale = db.scalars(
        select(Scan.id)
        .where(Scan.source_id == source.id, Scan.kind == "live")
        .order_by(Scan.started_at.desc(), Scan.id.desc())
        .offset(LIVE_SCAN_KEEP)
    ).all()
    if stale:
        db.execute(delete(Scan).where(Scan.id.in_(stale)))


def _get_or_create_scan(
    db: Session, source: Source, user: User, scan_uuid: str, kind: str = "full"
) -> Scan:
    """Scan-Lauf zur Client-Kennung laden oder anlegen (ersetzt das alte
    In-Memory-Register – funktioniert damit auch über mehrere Worker)."""
    scan = db.scalar(select(Scan).where(Scan.scan_uuid == scan_uuid))
    if scan is None:
        scan = Scan(
            source_id=source.id,
            scan_uuid=scan_uuid,
            started_by_user_id=user.id,
            kind=kind,
            # Der Erst-Import einer Quelle schreibt bewusst keine Change-Zeilen
            # (sonst eine pro Datei). Ein Live-Delta ist nie ein Erst-Import,
            # auch wenn die Quelle noch nie voll gescannt wurde – sonst gingen
            # genau die paar Änderungen verloren, die es melden soll.
            initial=kind == "full" and source.last_scanned_at is None,
        )
        db.add(scan)
        db.flush()
    elif scan.source_id != source.id:
        raise HTTPException(status_code=409, detail="scan_id gehört zu einer anderen Quelle")
    return scan


def _unique_by_key(entries: list[Entry], key) -> dict[tuple, Entry]:
    """Ordnet Einträge ihrem Schlüssel zu – mehrdeutige fallen heraus.

    Doppelte Schlüssel (z. B. zwei inhaltsgleiche Dateien) bleiben bewusst
    unzugeordnet: lieber „verschwunden + neu“ als eine falsche Zuordnung.
    ``key`` darf None liefern, wenn ein Eintrag nicht in Frage kommt.
    """
    seen: dict[tuple, Entry | None] = {}
    for e in entries:
        k = key(e)
        if k is None:
            continue
        seen[k] = None if k in seen else e  # None = mehrdeutig
    return {k: v for k, v in seen.items() if v is not None}


def _apply_move(db: Session, scan: Scan, old: Entry, new: Entry) -> None:
    """Zieht Annotationen zum neuen Eintrag um und macht aus „neu“ ein „verschoben“."""
    # Annotationen mitnehmen (Core-Update, damit der ORM-Cascade des alten
    # Eintrags sie nicht mitlöscht).
    db.execute(
        update(Annotation).where(Annotation.entry_id == old.id).values(entry_id=new.id)
    )
    # Ein bereits berechneter Hash gilt weiter – er hängt am Inhalt, nicht am
    # Pfad. Nur übernehmen, wenn er zum Stand der neuen Datei passt; sonst
    # holt ihn der nächste Hash-Nachlauf ohnehin neu.
    if old.hash_state and old.hash_size == new.size and old.hash_mtime == new.mtime:
        new.content_hash = old.content_hash
        new.hash_state = old.hash_state
        new.hash_size = old.hash_size
        new.hash_mtime = old.hash_mtime
    db.execute(
        delete(EntryChange).where(
            EntryChange.scan_id == scan.id,
            EntryChange.entry_id == new.id,
            EntryChange.change == "added",
        )
    )
    db.add(
        EntryChange(scan_id=scan.id, entry_id=new.id, change="moved", old_path=old.path)
    )
    db.execute(delete(Entry).where(Entry.id == old.id))
    scan.added -= 1
    scan.moved += 1


def _detect_moves(db: Session, scan: Scan, candidates: list[Entry]) -> tuple[int, set[int]]:
    """Umzug-Erkennung beim Finalize über (Name, Größe, mtime).

    Eine Datei, die verschwinden würde, und eine in diesem Scan neu
    aufgetauchte gelten als dieselbe, wenn der Schlüssel auf beiden Seiten
    eindeutig übereinstimmt. Dann wandern die Annotationen zum neuen Eintrag und
    der alte wird entfernt (statt als „verschwunden“ zu bleiben).

    Der **Inhalts-Hash** hilft hier bewusst nicht: neu angelegte Einträge haben
    noch keinen. Umbenannte Dateien (Name ändert sich, Inhalt nicht) fängt
    stattdessen ``reconcile_by_hash`` im Hash-Nachlauf ab.
    """
    added_entries = db.scalars(
        select(Entry)
        .join(EntryChange, EntryChange.entry_id == Entry.id)
        .where(
            EntryChange.scan_id == scan.id,
            EntryChange.change == "added",
            Entry.is_dir.is_(False),
        )
    ).all()
    if not added_entries:
        return 0, set()

    def meta_key(e: Entry) -> tuple:
        return (e.name, e.size, round(e.mtime))

    old_map = _unique_by_key([c for c in candidates if not c.is_dir], meta_key)
    new_map = _unique_by_key(list(added_entries), meta_key)

    moved = 0
    moved_old_ids: set[int] = set()
    for k, old in old_map.items():
        new = new_map.get(k)
        if new is None:
            continue
        _apply_move(db, scan, old, new)
        moved_old_ids.add(old.id)
        moved += 1
    return moved, moved_old_ids


@router.post("/{source_id}/ingest", response_model=IngestResult)
def ingest(
    source_id: int,
    batch: IngestBatchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nimmt eine Batch gescannter Einträge an und gleicht sie mit dem Index ab.

    Jeder Eintrag wird klassifiziert (neu / geändert / unverändert / wieder
    aufgetaucht); der Scan-Lauf zählt mit und schreibt ``entry_changes``
    (außer beim Erst-Scan einer Quelle). Bei ``finalize=True`` läuft die
    Umzug-Erkennung und alles nicht mehr Gesehene wird ``missing`` markiert
    (Annotationen bleiben).

    Der Desktop-Client nutzt denselben Endpunkt für zwei Betriebsarten:
    ``kind="full"`` ist der klassische Voll-Scan, ``kind="live"`` ein kleines
    Delta aus der Ordner-Überwachung. Ein Live-Delta zählt nur die Pfade auf,
    die es betrifft – Verschwundenes meldet es deshalb ausdrücklich über
    ``removed`` statt über die „alles nicht Gesehene“-Regel des Finalize.
    """
    source = _owned_source(db, user, source_id)
    kind = "live" if batch.kind == "live" else "full"
    scan = _get_or_create_scan(db, source, user, batch.scan_id, kind)
    now = utcnow()

    paths = [e.path for e in batch.entries]
    existing: dict[str, Entry] = {}
    if paths:
        existing = {
            en.path: en
            for en in db.scalars(
                select(Entry).where(
                    Entry.source_id == source.id, Entry.path.in_(paths)
                )
            ).all()
        }

    added = changed = reappeared = 0
    for e in batch.entries:
        ex = existing.get(e.path)
        if ex is None:
            entry = Entry(
                source_id=source.id,
                path=e.path,
                name=e.name,
                ext=e.ext,
                is_dir=e.is_dir,
                size=e.size,
                mtime=e.mtime,
                first_seen=now,
                last_seen=now,
                status="present",
                last_scan_id=scan.id,
            )
            db.add(entry)
            scan.added += 1
            added += 1
            if not scan.initial:
                db.flush()
                db.add(
                    EntryChange(
                        scan_id=scan.id,
                        entry_id=entry.id,
                        change="added",
                        new_size=e.size,
                        new_mtime=e.mtime,
                    )
                )
        else:
            was_missing = ex.status == "missing"
            meta_changed = (not e.is_dir) and (ex.size != e.size or ex.mtime != e.mtime)
            old_size, old_mtime = ex.size, ex.mtime
            ex.name = e.name
            ex.ext = e.ext
            ex.is_dir = e.is_dir
            ex.size = e.size
            ex.mtime = e.mtime
            ex.last_seen = now
            ex.last_scan_id = scan.id
            ex.status = "present"
            if was_missing:
                scan.reappeared += 1
                reappeared += 1
                db.add(
                    EntryChange(scan_id=scan.id, entry_id=ex.id, change="reappeared")
                )
            elif meta_changed:
                scan.changed += 1
                changed += 1
                db.add(
                    EntryChange(
                        scan_id=scan.id,
                        entry_id=ex.id,
                        change="modified",
                        old_size=old_size,
                        new_size=e.size,
                        old_mtime=old_mtime,
                        new_mtime=e.mtime,
                    )
                )
            else:
                scan.unchanged += 1

    # Übersprungene (nicht erreichbare) Einträge persistieren – dedupliziert
    # gegen bereits für diesen Scan gespeicherte Pfade (Batches/Retries).
    skipped_now = 0
    if batch.skipped:
        known = set(
            db.scalars(
                select(ScanSkip.path).where(ScanSkip.scan_id == scan.id)
            ).all()
        )
        for sk in batch.skipped:
            if sk.path in known:
                continue
            known.add(sk.path)
            db.add(
                ScanSkip(scan_id=scan.id, path=sk.path, reason=(sk.reason or "")[:80])
            )
            skipped_now += 1
        scan.skipped += skipped_now

    # Vom Client gemeldete Löschungen. Wie überall gilt: Einträge werden nie
    # gelöscht, nur als „verschwunden“ markiert – die Annotationen daran sollen
    # ein versehentliches Löschen überleben.
    removed_now = 0
    if batch.removed:
        gone = db.scalars(
            select(Entry).where(
                Entry.source_id == source.id,
                Entry.path.in_(batch.removed),
                Entry.status == "present",
            )
        ).all()
        for en in gone:
            en.status = "missing"
            db.add(EntryChange(scan_id=scan.id, entry_id=en.id, change="missing"))
            removed_now += 1
        scan.missing += removed_now

    marked_missing = moved = 0
    missing_check_skipped = False
    if batch.finalize:
        if batch.mark_missing and kind == "full":
            # Normalfall: nicht mehr gesehene Einträge als „verschwunden“
            # markieren (nach Umzug-Erkennung).
            db.flush()
            candidates = db.scalars(
                select(Entry).where(
                    Entry.source_id == source.id,
                    or_(Entry.last_scan_id.is_(None), Entry.last_scan_id != scan.id),
                    Entry.status == "present",
                )
            ).all()
            if candidates:
                moved, moved_ids = _detect_moves(db, scan, candidates)
                for en in candidates:
                    if en.id in moved_ids:
                        continue
                    en.status = "missing"
                    db.add(
                        EntryChange(scan_id=scan.id, entry_id=en.id, change="missing")
                    )
                    marked_missing += 1
                scan.missing += marked_missing
        elif kind == "full":
            # Unvollständiger Scan (Einträge waren nicht erreichbar): die
            # „verschwunden“-Erkennung wird ausgesetzt, damit nur kurz
            # unerreichbare Ordner nicht fälschlich als gelöscht gelten.
            missing_check_skipped = True
        scan.finished_at = now
        scan.status = "done"
        if kind == "full":
            # ``last_scanned_at`` heißt „zuletzt vollständig erfasst“ und trägt
            # zwei Dinge: die Dashboard-Zeile und die Erst-Scan-Erkennung. Ein
            # Live-Delta darf es deshalb nicht setzen – sonst gälte der erste
            # echte Voll-Scan nicht mehr als Erst-Import und schriebe eine
            # Change-Zeile je Datei.
            source.last_scanned_at = now
        else:
            _prune_live_scans(db, source)

    db.commit()
    return IngestResult(
        upserted=len(batch.entries),
        marked_missing=marked_missing,
        removed=removed_now,
        added=added,
        changed=changed,
        moved=moved,
        reappeared=reappeared,
        skipped=skipped_now,
        missing_check_skipped=missing_check_skipped,
    )


@router.get("/{source_id}/missing/summary")
def missing_summary(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Wie viele verschwundene Einträge gibt es – und wie viele davon tragen
    Annotationen? (Grundlage für den Aufräumen-Dialog.)"""
    from sqlalchemy import exists, func

    source = _owned_source(db, user, source_id)
    base = (Entry.source_id == source.id, Entry.status == "missing")
    total = db.scalar(select(func.count(Entry.id)).where(*base)) or 0
    has_ann = exists(select(Annotation.id).where(Annotation.entry_id == Entry.id))
    annotated = db.scalar(select(func.count(Entry.id)).where(*base, has_ann)) or 0
    return {"count": total, "annotated": annotated}


@router.post("/{source_id}/missing/cleanup")
def cleanup_missing(
    source_id: int,
    include_annotated: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verschwundene Einträge endgültig entfernen.

    Standardmäßig bleiben Einträge mit Annotationen erhalten (Notizen gehen
    nie stillschweigend verloren); ``include_annotated=true`` löscht auch sie.
    """
    from sqlalchemy import exists

    source = _owned_source(db, user, source_id)
    conds = [Entry.source_id == source.id, Entry.status == "missing"]
    if not include_annotated:
        conds.append(
            ~exists(select(Annotation.id).where(Annotation.entry_id == Entry.id))
        )
    ids = db.scalars(select(Entry.id).where(*conds)).all()
    if ids:
        db.execute(delete(Entry).where(Entry.id.in_(ids)))
        db.commit()
    return {"deleted": len(ids)}


# --- Inhalts-Hash -------------------------------------------------------------
#
# Das Hashen läuft bewusst NICHT im Scan mit: es muss jede Datei komplett lesen
# und würde den Scan um Größenordnungen verlangsamen. Stattdessen ein eigener,
# jederzeit unterbrechbarer Nachlauf: Der Server sagt, welche Pfade einen Hash
# brauchen (``/hash-todo``), der Browser rechnet SHA-256 und liefert nur den
# Hex-String zurück (``/hashes``) – der Inhalt verlässt den Rechner nie.

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Ein Hash gilt für genau den Dateistand, bei dem er berechnet wurde.
_HASH_STALE = or_(
    Entry.hash_state == "",
    Entry.hash_size.is_(None),
    Entry.hash_size != Entry.size,
    Entry.hash_mtime != Entry.mtime,
)


def reconcile_by_hash(db: Session, source: Source, entry: Entry) -> bool:
    """Ordnet einen frisch gehashten Eintrag einer verschwundenen Datei zu.

    Damit werden **Umbenennungen** erkannt, die die Metadaten-Heuristik des
    Scans nicht fassen kann (der Name ändert sich ja). Zugeordnet wird nur, wenn
    es genau eine verschwundene und genau eine vorhandene Datei mit diesem
    Inhalt gibt – bei mehreren Kandidaten bleibt es bewusst bei
    „verschwunden + neu“, statt zu raten.

    Gibt True zurück, wenn eine Zuordnung stattgefunden hat.
    """
    same_hash = db.scalars(
        select(Entry).where(
            Entry.source_id == source.id,
            Entry.content_hash == entry.content_hash,
            Entry.hash_state == "ok",
            Entry.is_dir.is_(False),
        )
    ).all()
    gone = [e for e in same_hash if e.status == "missing"]
    present = [e for e in same_hash if e.status == "present"]
    if len(gone) != 1 or len(present) != 1 or present[0].id != entry.id:
        return False

    old = gone[0]
    db.execute(
        update(Annotation).where(Annotation.entry_id == old.id).values(entry_id=entry.id)
    )
    db.execute(delete(Entry).where(Entry.id == old.id))
    return True


@router.get("/{source_id}/hash-todo", response_model=list[HashTodoOut])
def hash_todo(
    source_id: int,
    limit: int = Query(default=200, le=5000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dateien ohne gültigen Inhalts-Hash – die Arbeitsliste des Nachlaufs.

    Kleine Dateien zuerst: so wächst der Fortschritt sichtbar, und ein
    abgebrochener Lauf hat trotzdem etwas erledigt.
    """
    source = _owned_source(db, user, source_id)
    entries = db.scalars(
        select(Entry)
        .where(
            Entry.source_id == source.id,
            Entry.is_dir.is_(False),
            Entry.status == "present",
            _HASH_STALE,
        )
        .order_by(Entry.size, Entry.path)
        .limit(limit)
    ).all()
    return [
        HashTodoOut(entry_id=e.id, path=e.path, size=e.size, mtime=e.mtime)
        for e in entries
    ]


@router.post("/{source_id}/hashes")
def submit_hashes(
    source_id: int,
    batch: HashBatchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Nimmt berechnete Hashes entgegen und erkennt daraus Umbenennungen."""
    source = _owned_source(db, user, source_id)
    paths = [i.path for i in batch.items]
    if not paths:
        return {"updated": 0, "reconciled": 0}

    by_path = {
        e.path: e
        for e in db.scalars(
            select(Entry).where(Entry.source_id == source.id, Entry.path.in_(paths))
        ).all()
    }

    updated = 0
    reconciled = 0
    fresh: list[Entry] = []
    for item in batch.items:
        entry = by_path.get(item.path)
        if entry is None or entry.is_dir:
            continue  # zwischenzeitlich verschwunden/aufgeräumt
        state = item.state if item.state in {"ok", "skipped", "error"} else "error"
        digest = item.sha256.strip().lower()
        if state == "ok" and not _HEX64.match(digest):
            raise HTTPException(
                status_code=422, detail=f"Ungültiger SHA-256-Hash für {item.path}"
            )
        entry.content_hash = digest if state == "ok" else ""
        entry.hash_state = state
        # Der Hash wird gegen den **Index**-Stand vermerkt, nicht gegen die
        # Werte, die der Client beim Lesen gesehen hat. Das ist entscheidend:
        # ``_HASH_STALE`` vergleicht hash_size/hash_mtime mit entry.size/mtime –
        # trüge man hier abweichende Werte ein (etwa weil der Scan die Datei
        # nicht lesen konnte und 0 eingetragen hat), bliebe der Eintrag ewig in
        # der Arbeitsliste und der Nachlauf liefe endlos im Kreis.
        # Hat sich die Datei seit dem Scan wirklich geändert, fällt das beim
        # nächsten Scan auf: der aktualisiert size/mtime und entwertet den Hash.
        entry.hash_size = entry.size
        entry.hash_mtime = entry.mtime
        updated += 1
        if state == "ok":
            fresh.append(entry)

    db.flush()
    for entry in fresh:
        if entry.status == "present" and reconcile_by_hash(db, source, entry):
            reconciled += 1
    db.commit()
    return {"updated": updated, "reconciled": reconciled}


@router.get("/{source_id}/hash-summary", response_model=HashSummaryOut)
def hash_summary(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fortschritt des Hashens einer Quelle (+ Zahl der Duplikat-Gruppen)."""
    from sqlalchemy import func

    source = _accessible_source(db, user, source_id)
    base = (
        Entry.source_id == source.id,
        Entry.is_dir.is_(False),
        Entry.status == "present",
    )
    files = db.scalar(select(func.count(Entry.id)).where(*base)) or 0
    pending = db.scalar(select(func.count(Entry.id)).where(*base, _HASH_STALE)) or 0

    def state_count(state: str) -> int:
        return (
            db.scalar(
                select(func.count(Entry.id)).where(
                    *base, ~_HASH_STALE, Entry.hash_state == state
                )
            )
            or 0
        )

    groups = db.scalar(
        select(func.count()).select_from(
            select(Entry.content_hash)
            .where(*base, Entry.hash_state == "ok", Entry.content_hash != "")
            .group_by(Entry.content_hash)
            .having(func.count(Entry.id) > 1)
            .subquery()
        )
    ) or 0

    return HashSummaryOut(
        files=files,
        hashed=state_count("ok"),
        pending=pending,
        skipped=state_count("skipped"),
        errors=state_count("error"),
        duplicate_groups=groups,
    )


@router.post("/{source_id}/seen")
def mark_source_seen(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Quelle als angesehen markieren (Basis der Ungelesen-Punkte im Baum)."""
    source = _accessible_source(db, user, source_id)
    visit = db.scalar(
        select(SourceVisit).where(
            SourceVisit.user_id == user.id, SourceVisit.source_id == source.id
        )
    )
    if visit is None:
        db.add(SourceVisit(user_id=user.id, source_id=source.id, last_seen_at=utcnow()))
    else:
        visit.last_seen_at = utcnow()
    db.commit()
    return {"ok": True}


# --- Scan-Historie ------------------------------------------------------------

@router.get("/{source_id}/scans", response_model=list[ScanOut])
def list_scans(
    source_id: int,
    limit: int = Query(default=20, le=100),
    include_live: bool = Query(
        default=False, description="Live-Deltas des Desktop-Clients mit auflisten"
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Letzte Scan-Läufe einer Quelle (neueste zuerst), mit Diff-Zählern.

    Standardmäßig nur Voll-Scans – die Live-Deltas des Desktop-Clients würden
    die Historie sonst überschwemmen; ``include_live=true`` blendet sie ein.
    """
    source = _accessible_source(db, user, source_id)
    conds = [Scan.source_id == source.id]
    if not include_live:
        conds.append(Scan.kind == "full")
    scans = db.scalars(
        select(Scan)
        .where(*conds)
        .order_by(Scan.started_at.desc(), Scan.id.desc())
        .limit(limit)
    ).all()
    names = {
        u.id: u.display_name
        for u in db.scalars(
            select(User).where(
                User.id.in_({s.started_by_user_id for s in scans if s.started_by_user_id})
            )
        ).all()
    }
    outs = []
    for s in scans:
        out = ScanOut.model_validate(s)
        out.started_by_name = names.get(s.started_by_user_id)
        outs.append(out)
    return outs


@router.get("/{source_id}/scans/{scan_id}/changes", response_model=list[EntryChangeOut])
def scan_changes(
    source_id: int,
    scan_id: int,
    limit: int = Query(default=500, le=2000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Der Diff eines Scans: was kam hinzu, änderte sich, verschwand, zog um."""
    source = _accessible_source(db, user, source_id)
    scan = db.get(Scan, scan_id)
    if scan is None or scan.source_id != source.id:
        raise HTTPException(status_code=404, detail="Scan nicht gefunden")
    rows = db.execute(
        select(EntryChange, Entry)
        .join(Entry, Entry.id == EntryChange.entry_id)
        .where(EntryChange.scan_id == scan.id)
        .order_by(EntryChange.change, Entry.path)
        .limit(limit)
    ).all()
    return [
        EntryChangeOut(
            id=c.id,
            entry_id=e.id,
            change=c.change,
            path=e.path,
            name=e.name,
            is_dir=e.is_dir,
            old_path=c.old_path,
            old_size=c.old_size,
            new_size=c.new_size,
            created_at=c.created_at,
        )
        for c, e in rows
    ]


@router.get("/{source_id}/scans/{scan_id}/skips", response_model=list[ScanSkipOut])
def scan_skips(
    source_id: int,
    scan_id: int,
    limit: int = Query(default=1000, le=5000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Die bei einem Scan übersprungenen (nicht erreichbaren) Pfade.

    Grundlage für das Log-/Fehler-Popup: zeigt persistent, was z. B. wegen
    Netzwerk-Aussetzern auf einem Netzlaufwerk nicht erfasst werden konnte.
    """
    source = _accessible_source(db, user, source_id)
    scan = db.get(Scan, scan_id)
    if scan is None or scan.source_id != source.id:
        raise HTTPException(status_code=404, detail="Scan nicht gefunden")
    skips = db.scalars(
        select(ScanSkip)
        .where(ScanSkip.scan_id == scan.id)
        .order_by(ScanSkip.path)
        .limit(limit)
    ).all()
    return [ScanSkipOut.model_validate(s) for s in skips]


# --- Teilen (Freigaben) -----------------------------------------------------

@router.get("/{source_id}/members", response_model=list[MemberOut])
def list_members(
    source_id: int,
    path: str | None = Query(default=None, description="nur Mitglieder mit Zugriff auf diesen Pfad"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Besitzer + freigegebene Nutzer – mögliche Übergabe-Empfänger.

    Mit ``path`` werden nur Nutzer zurückgegeben, deren Freigabe diesen Pfad
    abdeckt (wichtig bei Teilbaum-Freigaben).
    """
    source = _accessible_source(db, user, source_id)
    ids = {source.owner_user_id}  # Besitzer ist immer Mitglied
    for sh in db.scalars(
        select(SourceShare).where(SourceShare.source_id == source.id)
    ).all():
        if path is None or path_in_scope(path, sh.path_prefix):
            ids.add(sh.user_id)
    members = db.scalars(select(User).where(User.id.in_(ids))).all()
    return [
        MemberOut(id=m.id, display_name=m.display_name, username=m.username, email=m.email)
        for m in members
    ]


@router.get("/{source_id}/shares", response_model=list[ShareOut])
def list_shares(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _owned_source(db, user, source_id)
    shares = db.scalars(
        select(SourceShare).where(SourceShare.source_id == source.id)
    ).all()
    users = {
        u.id: u
        for u in db.scalars(
            select(User).where(User.id.in_([s.user_id for s in shares]))
        ).all()
    }
    result = [
        ShareOut(
            user_id=s.user_id,
            email=users[s.user_id].email,
            username=users[s.user_id].username,
            display_name=users[s.user_id].display_name,
            permission=s.permission,
            path_prefix=s.path_prefix,
        )
        for s in shares
        if s.user_id in users
    ]
    # Ausstehende Einladungen (Empfänger hat noch kein Konto) mit anzeigen.
    for inv in db.scalars(
        select(Invite).where(Invite.source_id == source.id)
    ).all():
        result.append(
            ShareOut(
                email=inv.email,
                display_name=inv.email,
                permission=inv.permission,
                path_prefix=inv.path_prefix,
                pending=True,
                invite_id=inv.id,
            )
        )
    return result


@router.post("/{source_id}/shares", response_model=ShareOut, status_code=201)
def add_share(
    source_id: int,
    data: ShareIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _owned_source(db, user, source_id)
    if data.permission not in {"read", "annotate"}:
        raise HTTPException(status_code=422, detail="Berechtigung muss read|annotate sein")

    prefix = data.path_prefix.strip("/")
    if prefix:
        # Teilbaum-Freigabe: der Ordner muss in der Quelle existieren.
        exists = db.scalar(
            select(Entry.id).where(Entry.source_id == source.id, Entry.path == prefix)
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="Ordner in dieser Quelle nicht gefunden")

    if not data.who:
        raise HTTPException(status_code=422, detail="E-Mail oder Username angeben")
    target = find_user(db, data.who)
    if target is None:
        # Unbekannte E-Mail -> Einladung; greift automatisch bei Registrierung.
        who = data.who.strip().lower()
        if "@" not in who:
            raise HTTPException(status_code=404, detail="Kein Nutzer mit diesem Username")
        invite = db.scalar(
            select(Invite).where(
                Invite.email == who,
                Invite.source_id == source.id,
                Invite.path_prefix == prefix,
            )
        )
        if invite is None:
            invite = Invite(
                email=who,
                source_id=source.id,
                path_prefix=prefix,
                permission=data.permission,
                invited_by_user_id=user.id,
            )
            db.add(invite)
        else:
            invite.permission = data.permission
        db.commit()
        db.refresh(invite)
        return ShareOut(
            email=who,
            display_name=who,
            permission=data.permission,
            path_prefix=prefix,
            pending=True,
            invite_id=invite.id,
        )
    if target.id == source.owner_user_id:
        raise HTTPException(status_code=400, detail="Die Quelle gehört dir bereits")

    share = db.scalar(
        select(SourceShare).where(
            SourceShare.source_id == source.id,
            SourceShare.user_id == target.id,
            SourceShare.path_prefix == prefix,
        )
    )
    if share is None:
        share = SourceShare(
            source_id=source.id,
            user_id=target.id,
            path_prefix=prefix,
            permission=data.permission,
        )
        db.add(share)
    else:
        share.permission = data.permission  # bestehende Freigabe aktualisieren
    db.commit()
    return ShareOut(
        user_id=target.id,
        email=target.email,
        username=target.username,
        display_name=target.display_name,
        permission=data.permission,
        path_prefix=prefix,
    )


@router.delete("/{source_id}/invites/{invite_id}", status_code=204)
def remove_invite(
    source_id: int,
    invite_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _owned_source(db, user, source_id)
    invite = db.get(Invite, invite_id)
    if invite is None or invite.source_id != source.id:
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden")
    db.delete(invite)
    db.commit()


@router.delete("/{source_id}/shares/{user_id}", status_code=204)
def remove_share(
    source_id: int,
    user_id: int,
    path_prefix: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = _owned_source(db, user, source_id)
    share = db.scalar(
        select(SourceShare).where(
            SourceShare.source_id == source.id,
            SourceShare.user_id == user_id,
            SourceShare.path_prefix == path_prefix.strip("/"),
        )
    )
    if share is None:
        raise HTTPException(status_code=404, detail="Freigabe nicht gefunden")
    db.delete(share)
    db.commit()
