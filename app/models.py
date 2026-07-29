"""SQLAlchemy-2.0-Modelle für filetree_coop."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # Kleingeschriebener, eindeutiger Anmeldename (alternativ zur E-Mail).
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Bis wann der Nutzer den Aktivitäts-Feed gesehen hat (Badge-Berechnung).
    last_activity_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    sources: Mapped[list["Source"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Source(Base):
    """Ein registriertes Filesystem-Wurzelverzeichnis auf irgendeinem Rechner."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(200))
    # local | network | bwsync | shared
    kind: Mapped[str] = mapped_column(String(30), default="local")
    # freier Hinweis auf Rechner/Ort, z. B. "Laptop Max" oder "Netzlaufwerk P:"
    host_hint: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    owner: Mapped["User"] = relationship(back_populates="sources")
    entries: Mapped[list["Entry"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    shares: Mapped[list["SourceShare"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class SourceShare(Base):
    """Teilt eine Quelle – ganz oder einen Teilbaum – mit einem anderen Nutzer.

    ``path_prefix`` == "" bedeutet die ganze Quelle; sonst gilt die Freigabe nur
    für den Ordner unter diesem Pfad und alles darunter.
    """

    __tablename__ = "source_shares"
    __table_args__ = (
        UniqueConstraint("source_id", "user_id", "path_prefix", name="uq_share"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # "" = ganze Quelle; sonst der freigegebene Teilbaum (relativer Ordnerpfad).
    path_prefix: Mapped[str] = mapped_column(Text, default="")
    permission: Mapped[str] = mapped_column(String(20), default="read")  # read|annotate

    source: Mapped["Source"] = relationship(back_populates="shares")


class Entry(Base):
    """Eine Datei oder ein Ordner innerhalb einer Quelle."""

    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("source_id", "path", name="uq_entry_path"),
        # Trägt die Duplikat-Suche (gleicher Inhalt innerhalb/über Quellen).
        Index("ix_entries_source_content_hash", "source_id", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)  # relativ zur Quell-Wurzel
    name: Mapped[str] = mapped_column(String(500), index=True)
    ext: Mapped[str] = mapped_column(String(50), default="")
    is_dir: Mapped[bool] = mapped_column(Boolean, default=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float] = mapped_column(default=0.0)  # epoch seconds
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="present")  # present|missing
    # Letzter Scan-Lauf, der diesen Eintrag gesehen hat. Grundlage der
    # „verschwunden“-Erkennung (statt Uhrzeit-Vergleich, der bei schnell
    # aufeinanderfolgenden Scans durch Uhr-Auflösung ins Wanken geraten kann).
    last_scan_id: Mapped[int | None] = mapped_column(
        ForeignKey("scans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # --- Inhalts-Hash (optional, im Browser berechnet) ----------------------
    # SHA-256 als Hex; "" = keiner. Der Hash entsteht NICHT beim Scan, sondern
    # in einem eigenen Nachlauf (siehe /hash-todo), weil er die Dateien lesen
    # muss. ``hash_size``/``hash_mtime`` halten fest, für welchen Stand der Hash
    # gilt – weicht die Datei davon ab, ist er veraltet und wird neu berechnet.
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    # "" = noch nie versucht | ok | skipped (zu groß) | error (nicht lesbar)
    hash_state: Mapped[str] = mapped_column(String(20), default="")
    hash_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hash_mtime: Mapped[float | None] = mapped_column(nullable=True)

    source: Mapped["Source"] = relationship(back_populates="entries")
    annotations: Mapped[list["Annotation"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )


class Annotation(Base):
    """Notiz/Todo/Label/Übergabe an einer Datei."""

    __tablename__ = "annotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # None = freie Notiz ohne Datei-Bezug (nur bei type == "note").
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("entries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Antwort auf eine andere Annotation desselben Eintrags (Thread).
    parent_annotation_id: Mapped[int | None] = mapped_column(
        ForeignKey("annotations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    author_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # note | todo | label | handover
    type: Mapped[str] = mapped_column(String(20), default="note")
    body: Mapped[str] = mapped_column(Text, default="")
    label_value: Mapped[str] = mapped_column(String(120), default="")
    assignee_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    # Workflow-Status einer Übergabe: open | accepted | done.
    status: Mapped[str] = mapped_column(String(20), default="open")
    # Fälligkeit (v. a. für Todos/Übergaben); None = ohne Termin.
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # Pinnwand-Farbe freier/angehefteter Notizen (z. B. "yellow"); "" = Standard.
    color: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    entry: Mapped["Entry | None"] = relationship(back_populates="annotations")
    shares: Mapped[list["AnnotationShare"]] = relationship(
        back_populates="annotation", cascade="all, delete-orphan"
    )


class AnnotationShare(Base):
    """Teilt eine freie Notiz (``entry_id is None``) mit einem Kollegen.

    Anders als bei Quellen gibt es hier keine Berechtigungsstufen – geteilt
    heißt lesend sichtbar; bearbeiten/löschen bleibt dem Autor vorbehalten.
    """

    __tablename__ = "annotation_shares"
    __table_args__ = (
        UniqueConstraint("annotation_id", "user_id", name="uq_annotation_share"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    annotation_id: Mapped[int] = mapped_column(
        ForeignKey("annotations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    annotation: Mapped["Annotation"] = relationship(back_populates="shares")


class Scan(Base):
    """Ein Scan-Lauf einer Quelle.

    Ersetzt das frühere In-Memory-Register (funktioniert damit auch mit
    mehreren Workern) und hält den Diff des Laufs als Zähler fest.
    """

    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    # Vom Client vergebene Kennung; alle Batches eines Laufs teilen sie.
    scan_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    started_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|done
    # Erst-Scan einer Quelle: es werden keine per-Eintrag-Change-Zeilen
    # geschrieben (sonst eine Zeile pro importierter Datei).
    initial: Mapped[bool] = mapped_column(Boolean, default=False)
    # "full"  = vollständiger Lauf über den ganzen Baum (Browser oder Client);
    # "live"  = kleines Delta des Desktop-Clients aus der Ordner-Überwachung.
    # Live-Läufe sind bewusst als eigene Art markiert: sie fallen im Minutentakt
    # an und würden sonst Dashboard-Diff und Aktivitäts-Feed zumüllen.
    kind: Mapped[str] = mapped_column(String(10), default="full")
    added: Mapped[int] = mapped_column(Integer, default=0)
    changed: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    missing: Mapped[int] = mapped_column(Integer, default=0)
    moved: Mapped[int] = mapped_column(Integer, default=0)
    reappeared: Mapped[int] = mapped_column(Integer, default=0)
    # Wie viele Einträge während des Scans nicht erreichbar waren und
    # übersprungen wurden (z. B. Netzwerk-Aussetzer auf einem Netzlaufwerk).
    skipped: Mapped[int] = mapped_column(Integer, default=0)


class ScanSkip(Base):
    """Ein während eines Scans übersprungener (nicht erreichbarer) Eintrag.

    Persistiert die Pfade, damit das UI auch nach einem Reload noch anzeigen
    kann, was beim Scan eines Netzlaufwerks nicht erfasst werden konnte.
    """

    __tablename__ = "scan_skips"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)  # relativ zur Quell-Wurzel ("" = Wurzel)
    reason: Mapped[str] = mapped_column(String(80), default="")  # z. B. "NotFoundError"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EntryChange(Base):
    """Eine im Scan festgestellte Änderung – speist Scan-Diff und Datei-Historie."""

    __tablename__ = "entry_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("entries.id", ondelete="CASCADE"), index=True
    )
    # added | modified | missing | moved | reappeared
    change: Mapped[str] = mapped_column(String(20))
    old_path: Mapped[str] = mapped_column(Text, default="")  # bei moved
    old_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_mtime: Mapped[float | None] = mapped_column(nullable=True)
    new_mtime: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SourceVisit(Base):
    """Wann ein Nutzer eine Quelle zuletzt angesehen hat (Ungelesen-Marker)."""

    __tablename__ = "source_visits"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", name="uq_source_visit"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IgnoreRule(Base):
    """Ein gespeicherter Ausschluss aus der Suche (Ordnerbaum oder Dateiname).

    Regeln gehören dem Nutzer, sind dauerhaft und wirken auf **jede** seiner
    Suchen – auch auf die des Suchassistenten. Sie löschen nichts und ändern den
    Index nicht; sie blenden nur aus.

    - ``kind == "path"``: ein Ordner (oder eine Datei) samt allem darunter.
      Klartext-Pfade wirken als Präfix (``Archiv/2019`` blendet
      ``Archiv/2019/alt/x.pdf`` mit aus), Muster mit Platzhaltern über Regex
      (``**/node_modules``).
    - ``kind == "name"``: ein Dateiname als Glob-Muster (``*.tmp``,
      ``.DS_Store``) – unabhängig davon, wo er liegt.

    ``source_id is None`` heißt: gilt in allen Quellen.
    """

    __tablename__ = "ignore_rules"
    __table_args__ = (
        Index("ix_ignore_rules_user_active", "user_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # None = alle Quellen; sonst nur diese eine.
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(10), default="path")  # path | name
    pattern: Mapped[str] = mapped_column(Text)
    # Vorübergehend abschaltbar, ohne die Regel zu verlieren.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Invite(Base):
    """Ausstehende Freigabe an eine E-Mail ohne Konto.

    Wird bei der Registrierung dieser E-Mail automatisch in eine echte
    ``SourceShare`` umgewandelt.
    """

    __tablename__ = "invites"
    __table_args__ = (
        UniqueConstraint("email", "source_id", "path_prefix", name="uq_invite"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    path_prefix: Mapped[str] = mapped_column(Text, default="")
    permission: Mapped[str] = mapped_column(String(20), default="annotate")
    invited_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --- Desktop-Client (Agent) --------------------------------------------------
#
# Der Desktop-Client ist ein Hintergrundprogramm auf dem Rechner des Nutzers:
# er überwacht konfigurierte Ordner, schickt Metadaten-Änderungen an den Server
# und kann auf Zuruf einen Ordner im Explorer öffnen. Die *maßgebliche*
# Konfiguration liegt beim Client (er kennt seine lokalen Pfade); der Server
# hält davon eine gemeldete Kopie, damit die Website zeigen kann, welcher Client
# wo läuft, und damit ein Befehl an den richtigen Client geht.


class Client(Base):
    """Ein registrierter Desktop-Client (ein Rechner eines Nutzers).

    Authentifiziert sich mit einem Gerätetoken, der nur als Hash gespeichert
    wird (``token_hash``) – wie ein Passwort, damit ein DB-Leck keine
    einsatzfähigen Tokens preisgibt. „Online“ ist keine Spalte, sondern wird aus
    ``last_seen_at`` abgeleitet (siehe ``routers/clients.py``): eine gespeicherte
    Flagge bliebe hängen, wenn ein Client abstürzt statt sich abzumelden.
    """

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Frei wählbarer Anzeigename, z. B. "Laptop Max".
    name: Mapped[str] = mapped_column(String(120), default="")
    hostname: Mapped[str] = mapped_column(String(200), default="")
    platform: Mapped[str] = mapped_column(String(40), default="")  # win32|darwin|linux
    version: Mapped[str] = mapped_column(String(40), default="")
    # SHA-256 des Gerätetokens (hex). Der Token selbst wird nur einmal ausgegeben.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Kurzer Zustandstext des Clients ("2 Ordner überwacht", "pausiert" …).
    status_text: Mapped[str] = mapped_column(String(300), default="")
    # Vom Nutzer angehalten? Der Client fragt das beim Heartbeat ab und legt
    # seine Sync-Worker still – so lässt sich ein Rechner aus der Ferne pausieren.
    paused: Mapped[bool] = mapped_column(Boolean, default=False)

    folders: Mapped[list["ClientFolder"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class ClientFolder(Base):
    """Ein vom Client überwachter Ordner, gebunden an eine Quelle.

    Gemeldete Kopie der Client-Konfiguration – die Website zeigt daraus „welcher
    Rechner deckt welche Quelle mit welchem lokalen Pfad ab“. ``enabled`` und
    ``hash_enabled`` sind getrennt schaltbar: den Index aktuell halten ist
    billig, Inhalts-Hashes lesen jede Datei komplett.
    """

    __tablename__ = "client_folders"
    __table_args__ = (
        UniqueConstraint("client_id", "source_id", name="uq_client_folder"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    # Absoluter Pfad auf dem Client-Rechner (nur der Besitzer sieht ihn).
    local_path: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    hash_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Live-Überwachung (watchdog) zusätzlich zum periodischen Voll-Scan.
    watch_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Letzter Fehler dieses Ordners (z. B. "Pfad nicht gefunden"); "" = alles gut.
    last_error: Mapped[str] = mapped_column(String(300), default="")

    client: Mapped["Client"] = relationship(back_populates="folders")


class ClientCommand(Base):
    """Ein Auftrag vom Server an einen Client (z. B. „öffne diesen Ordner“).

    Der Client holt offene Aufträge beim Heartbeat ab – bewusst per Polling
    statt WebSocket: das funktioniert hinter jedem Proxy und NAT, ohne dass der
    Client von außen erreichbar sein muss. Die Latenz ist das Heartbeat-Intervall
    (wenige Sekunden), was für „Ordner öffnen“ genügt.
    """

    __tablename__ = "client_commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    # open_folder | rescan | rehash
    command: Mapped[str] = mapped_column(String(30))
    payload_json: Mapped[str] = mapped_column(Text, default="")
    # pending -> delivered -> done | error (| expired)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result: Mapped[str] = mapped_column(String(500), default="")
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --- LLM-Integration --------------------------------------------------------
#
# Der gesamte Block ist bewusst generisch gehalten: ``LLMConnection`` ist die
# reine API-Anbindung (URL/Token/Typ), ``LLMSetting`` ein auswählbares
# Inferenz-Profil (Verbindung + Modell + Parameter), ``LLMPrompt`` eine
# wiederverwendbare Vorlage. ``LLMFeatureLink`` macht Settings/Prompts pro
# Feature (z. B. "notes") verfügbar, und ``AIRun`` protokolliert jeden Lauf –
# feature-unabhängig über (``target_kind``, ``target_id``). Alles nutzerbezogen.


class LLMConnection(Base):
    """Eine konfigurierte LLM-API-Anbindung eines Nutzers.

    ``api_key_enc`` hält den Token nicht im Klartext, sondern verschleiert
    (siehe ``app.llm.crypto``). JSON-Felder werden als Text abgelegt, passend
    zum sonstigen Umgang der App mit SQLite.
    """

    __tablename__ = "llm_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120))
    # openai | anthropic | openai_compatible | ollama | custom
    provider_type: Mapped[str] = mapped_column(String(30), default="openai")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    # Verschleierter API-Token; None/"" wenn der Endpunkt keinen braucht (Ollama).
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model: Mapped[str] = mapped_column(String(200), default="")
    # Zusätzliche Header / API-Version etc. als JSON-Objekt (Text).
    extra_json: Mapped[str] = mapped_column(Text, default="")
    # Zuletzt abgerufene Modellliste als JSON (Liste von IDs) + Zeitstempel.
    models_cache_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class LLMSetting(Base):
    """Auswählbares Inferenz-Profil: Verbindung + Modell + Parameter.

    Das ist die Entität, die in den Feature-Dropdowns ("LLM-Setting") erscheint.
    """

    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("llm_connections.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(200), default="")
    # Optionaler Basis-System-Prompt, der jedem Lauf vorangestellt wird.
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    # temperature/max_tokens/top_p … als JSON-Objekt (Text).
    params_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class LLMPrompt(Base):
    """Benannte, wiederverwendbare Prompt-Vorlage.

    ``body`` enthält den Platzhalter ``{{input}}`` für den eingespeisten Text.
    """

    __tablename__ = "llm_prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class LLMFeatureLink(Base):
    """Macht ein Setting oder einen Prompt in einem Feature verfügbar.

    ``kind`` == "setting" | "prompt", ``ref_id`` die jeweilige Zeile,
    ``feature_key`` ein freier Bezeichner (z. B. "notes"). Dadurch lässt sich
    jede spätere Funktion anbinden, ohne das Schema zu ändern.
    """

    __tablename__ = "llm_feature_links"
    __table_args__ = (
        UniqueConstraint(
            "kind", "ref_id", "feature_key", name="uq_llm_feature_link"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # setting | prompt
    ref_id: Mapped[int] = mapped_column(Integer, index=True)
    feature_key: Mapped[str] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AIRun(Base):
    """Ein generischer LLM-Lauf samt Ergebnis – das Rückgrat der Wiederverwendung.

    Über (``target_kind``, ``target_id``) an ein beliebiges Objekt gebunden
    (z. B. ("annotation", 42)); ``target_id`` bleibt None bei ungebundenen
    Läufen. Der KI-Tab einer Notiz zeigt einfach den neuesten Lauf für ihr Ziel.
    """

    __tablename__ = "ai_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # z. B. "annotation"; freier Bezeichner, kein FK (feature-unabhängig).
    target_kind: Mapped[str] = mapped_column(String(30), default="", index=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    setting_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_settings.id", ondelete="SET NULL"), nullable=True
    )
    prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_prompts.id", ondelete="SET NULL"), nullable=True
    )
    input_text: Mapped[str] = mapped_column(Text, default="")
    output_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | error
    error: Mapped[str] = mapped_column(Text, default="")
    # Kontext für die Anzeige (Modell/Prompt-Name), auch wenn Quelle gelöscht wird.
    meta_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
