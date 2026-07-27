"""Pydantic-Schemas für die API-Ein-/Ausgabe."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# --- Auth -------------------------------------------------------------------

class RegisterIn(BaseModel):
    email: EmailStr
    # Buchstaben/Ziffern/._- , 3–30 Zeichen; wird kleingeschrieben gespeichert.
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,30}$")
    password: str = Field(min_length=6)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    # E-Mail oder Username.
    identifier: str = Field(min_length=1)
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    username: str
    display_name: str


class ProfileUpdateIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,30}$")
    email: EmailStr


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


# --- Sources ----------------------------------------------------------------

class SourceIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="local")
    host_hint: str = Field(default="", max_length=200)


class ScanOut(BaseModel):
    """Ein Scan-Lauf mit seinem Diff (Zähler je Änderungsart)."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    initial: bool
    added: int
    changed: int
    unchanged: int
    missing: int
    moved: int
    reappeared: int
    skipped: int = 0
    started_by_name: str | None = None


class ScanSkipOut(BaseModel):
    """Ein während eines Scans übersprungener Eintrag (Pfad + Grund)."""
    model_config = ConfigDict(from_attributes=True)
    path: str
    reason: str = ""
    created_at: datetime


class EntryChangeOut(BaseModel):
    """Eine Änderung aus einem Scan, angereichert um Datei-Infos."""
    id: int
    entry_id: int
    change: str  # added|modified|missing|moved|reappeared
    path: str
    name: str
    is_dir: bool
    old_path: str = ""
    old_size: int | None = None
    new_size: int | None = None
    created_at: datetime


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_user_id: int
    label: str
    kind: str
    host_hint: str
    last_scanned_at: datetime | None = None
    # Letzter abgeschlossener Scan (für die Diff-Zeile im Dashboard).
    last_scan: ScanOut | None = None


class ShareIn(BaseModel):
    # Kollege per E-Mail ODER Username. `email` bleibt als Alias erhalten.
    identifier: str | None = None
    email: EmailStr | None = None
    permission: str = Field(default="annotate")  # read | annotate
    path_prefix: str = ""  # "" = ganze Quelle; sonst freigegebener Teilbaum

    @property
    def who(self) -> str | None:
        return self.identifier or self.email


class ShareOut(BaseModel):
    # user_id ist None bei ausstehenden Einladungen (Empfänger ohne Konto).
    user_id: int | None = None
    email: EmailStr
    username: str = ""
    display_name: str = ""
    permission: str
    path_prefix: str = ""
    pending: bool = False
    invite_id: int | None = None


class MyShareOut(ShareOut):
    """Eine ausgehende Freigabe mit Quell-Kontext – für die Profil-Übersicht
    aller Inhalte, die der Nutzer über all seine Quellen hinweg geteilt hat."""
    source_id: int
    source_label: str


class MemberOut(BaseModel):
    id: int
    display_name: str
    username: str
    email: EmailStr


# --- Ingest -----------------------------------------------------------------

class EntryIn(BaseModel):
    path: str
    name: str
    is_dir: bool = False
    size: int = 0
    mtime: float = 0.0
    ext: str = ""


class SkipIn(BaseModel):
    path: str
    reason: str = ""


class IngestBatchIn(BaseModel):
    entries: list[EntryIn]
    # True bei der letzten Batch eines Scans -> markiert nicht gesehene als missing.
    finalize: bool = False
    # Kennung dieses Scan-Laufs; alle Batches eines Scans teilen dieselbe ID.
    scan_id: str
    # Nicht erreichbare Einträge, die der Scanner überspringen musste.
    skipped: list[SkipIn] = []
    # Beim Finalize nicht gesehene Einträge als „verschwunden“ markieren?
    # Bei unvollständigen Scans (Skips) setzt der Client dies auf False, damit
    # nur kurz unerreichbare Ordner nicht fälschlich als gelöscht gelten.
    mark_missing: bool = True


class IngestResult(BaseModel):
    upserted: int
    marked_missing: int = 0
    # Diff dieses Aufrufs (kumulativ steht er am Scan selbst).
    added: int = 0
    changed: int = 0
    moved: int = 0
    reappeared: int = 0
    skipped: int = 0
    # True, wenn die „verschwunden“-Erkennung bei diesem Finalize ausgesetzt
    # wurde (unvollständiger Scan wegen nicht erreichbarer Einträge).
    missing_check_skipped: bool = False


# --- Inhalts-Hash -----------------------------------------------------------

class HashTodoOut(BaseModel):
    """Eine Datei, für die (noch) kein gültiger Hash vorliegt."""
    entry_id: int
    path: str
    size: int
    mtime: float


class HashItemIn(BaseModel):
    path: str
    # SHA-256 als Hex-String (64 Zeichen); leer bei state != "ok".
    sha256: str = ""
    state: str = "ok"  # ok | skipped | error
    # Stand der Datei, für den der Hash gilt (erkennt spätere Änderungen).
    size: int = 0
    mtime: float = 0.0


class HashBatchIn(BaseModel):
    items: list[HashItemIn]


class HashSummaryOut(BaseModel):
    """Fortschritt des Hashens je Quelle (Basis für die Dashboard-Anzeige)."""
    files: int = 0  # vorhandene Dateien (ohne Ordner)
    hashed: int = 0  # gültiger Hash zum aktuellen Dateistand
    pending: int = 0  # kein oder veralteter Hash
    skipped: int = 0  # bewusst übersprungen (zu groß)
    errors: int = 0  # nicht lesbar
    duplicate_groups: int = 0  # Gruppen gleicher Inhalte in dieser Quelle


# --- Annotations ------------------------------------------------------------

class AnnotationIn(BaseModel):
    # None = freie Notiz ohne Datei-Bezug (nur erlaubt bei type == "note").
    entry_id: int | None = None
    type: str = Field(default="note")  # note|todo|label|handover
    body: str = ""
    label_value: str = ""
    assignee_user_id: int | None = None
    due_date: date | None = None  # Fälligkeit, ISO-Datum ("2026-07-31")
    # Antwort auf eine bestehende Annotation desselben Eintrags (eine Ebene tief).
    parent_annotation_id: int | None = None
    color: str = ""  # Pinnwand-Farbe, z. B. "yellow"; "" = Standard


class AnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entry_id: int | None
    author_user_id: int
    author_name: str | None = None
    author_username: str | None = None
    type: str
    body: str
    label_value: str
    assignee_user_id: int | None
    assignee_name: str | None = None
    done: bool
    # Workflow-Status einer Übergabe: open | accepted | done.
    status: str = "open"
    # Antwort auf eine andere Annotation (Thread).
    parent_annotation_id: int | None = None
    due_date: date | None = None
    color: str = ""
    created_at: datetime
    updated_at: datetime


class AnnotationRich(AnnotationOut):
    """Annotation angereichert um Datei-/Quellinfo – für die Übersichtsseite."""
    entry_name: str
    entry_path: str
    entry_status: str
    source_id: int
    source_label: str


class NoteOut(AnnotationOut):
    """Notiz für die Pinnwand – frei oder an eine Datei geheftet.

    Bei freien Notizen (``entry_id is None``) bleiben die Datei-/Quellfelder
    leer.
    """
    entry_name: str | None = None
    entry_path: str | None = None
    entry_status: str | None = None
    source_id: int | None = None
    source_label: str | None = None
    # True, wenn der aktuelle Nutzer der Autor ist (steuert Bearbeiten/Teilen im UI).
    is_mine: bool = False
    # Nur bei freien, eigenen Notizen gepflegt: Anzahl Kollegen mit Zugriff.
    share_count: int = 0


class AnnotationShareIn(BaseModel):
    identifier: str = Field(min_length=1)  # E-Mail oder Username


class AnnotationShareOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    email: EmailStr


class LabelCount(BaseModel):
    value: str
    count: int


# --- LLM-Integration --------------------------------------------------------

class LLMConnectionIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    provider_type: str = Field(default="openai", max_length=30)
    base_url: str = Field(default="", max_length=500)
    # Klartext-Token nur beim Anlegen/Ändern; wird verschlüsselt gespeichert
    # und nie zurückgegeben. None/"" = keinen Token setzen.
    api_key: str | None = None
    default_model: str = Field(default="", max_length=200)
    # Zusätzliche Header / API-Version etc.
    extra: dict = Field(default_factory=dict)


class LLMConnectionPatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    provider_type: str | None = Field(default=None, max_length=30)
    base_url: str | None = Field(default=None, max_length=500)
    # Anwesenheit zählt (siehe model_fields_set): "" löscht den Token, None lässt
    # ihn unverändert, ein Wert ersetzt ihn.
    api_key: str | None = None
    default_model: str | None = Field(default=None, max_length=200)
    extra: dict | None = None


class LLMConnectionOut(BaseModel):
    id: int
    label: str
    provider_type: str
    base_url: str
    default_model: str
    extra: dict = Field(default_factory=dict)
    # Nie der Token selbst – nur ob einer hinterlegt ist (+ maskierter Hinweis).
    has_key: bool = False
    key_hint: str = ""
    models: list[str] = []
    models_fetched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LLMConnectionTestOut(BaseModel):
    ok: bool
    detail: str = ""
    models_count: int | None = None


class LLMModelsOut(BaseModel):
    supported: bool
    models: list[str] = []


class LLMSettingIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    connection_id: int
    model: str = Field(default="", max_length=200)
    system_prompt: str = ""
    params: dict = Field(default_factory=dict)
    # Feature-Schlüssel, in denen dieses Setting auswählbar sein soll.
    features: list[str] = Field(default_factory=list)


class LLMSettingPatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    connection_id: int | None = None
    model: str | None = Field(default=None, max_length=200)
    system_prompt: str | None = None
    params: dict | None = None
    features: list[str] | None = None


class LLMSettingOut(BaseModel):
    id: int
    label: str
    connection_id: int
    connection_label: str = ""
    model: str
    system_prompt: str = ""
    params: dict = Field(default_factory=dict)
    features: list[str] = []
    created_at: datetime
    updated_at: datetime


class LLMPromptIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    body: str = ""
    description: str = Field(default="", max_length=300)
    features: list[str] = Field(default_factory=list)


class LLMPromptPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    body: str | None = None
    description: str | None = Field(default=None, max_length=300)
    features: list[str] | None = None


class LLMPromptOut(BaseModel):
    id: int
    name: str
    body: str
    description: str = ""
    features: list[str] = []
    created_at: datetime
    updated_at: datetime


class LLMRunIn(BaseModel):
    setting_id: int
    # Entweder ein gespeicherter Prompt ODER ad-hoc Prompt-Text (oder keiner).
    prompt_id: int | None = None
    prompt_text: str | None = None
    input_text: str = ""
    # Optionale Bindung an ein Objekt, z. B. ("annotation", 42).
    target_kind: str = Field(default="", max_length=30)
    target_id: int | None = None
    # Lauf im Verlauf (ai_runs) festhalten?
    persist: bool = True


class LLMRunOut(BaseModel):
    run_id: int | None = None
    output_text: str
    status: str = "ok"
    model: str = ""
    created_at: datetime | None = None


class AIRunOut(BaseModel):
    id: int
    target_kind: str = ""
    target_id: int | None = None
    setting_id: int | None = None
    prompt_id: int | None = None
    input_text: str = ""
    output_text: str = ""
    status: str = "ok"
    error: str = ""
    meta: dict = Field(default_factory=dict)
    created_at: datetime


class LLMFeatureOptionOut(BaseModel):
    """Für Feature-Dropdowns: verfügbare Settings + Prompts eines Features."""
    settings: list[LLMSettingOut] = []
    prompts: list[LLMPromptOut] = []


# --- Speicherplatz ----------------------------------------------------------

class StorageSourceOut(BaseModel):
    source_id: int
    label: str
    size: int
    files: int


class StorageSummaryOut(BaseModel):
    """Kennzahlen über alle zugänglichen (oder eine) Quelle(n)."""
    total_size: int = 0
    files: int = 0
    dirs: int = 0
    # Verschwundene Dateien belegen nichts mehr – nur zur Einordnung.
    missing: int = 0
    missing_size: int = 0
    sources: list[StorageSourceOut] = []


class FolderChildOut(BaseModel):
    name: str
    path: str
    is_dir: bool
    # Bei Ordnern die rekursive Summe, bei Dateien ihre eigene Größe.
    size: int
    files: int


class FolderLevelOut(BaseModel):
    parent: str = ""
    total_size: int = 0
    children: list[FolderChildOut] = []


class StorageEntryOut(BaseModel):
    entry_id: int
    source_id: int
    source_label: str
    path: str
    name: str
    ext: str = ""
    size: int
    mtime: float = 0.0


class TypeStatOut(BaseModel):
    ext: str
    size: int
    files: int


class AgeBucketOut(BaseModel):
    label: str
    days: int | None = None
    size: int
    files: int


class DuplicateMemberOut(BaseModel):
    entry_id: int
    source_id: int
    source_label: str
    path: str
    name: str


class DuplicateGroupOut(BaseModel):
    content_hash: str
    size: int
    count: int
    # Größe × (Kopien − 1) = so viel gäbe eine Bereinigung frei.
    wasted: int
    entries: list[DuplicateMemberOut] = []


# --- Search -----------------------------------------------------------------

class SearchHit(BaseModel):
    entry_id: int
    source_id: int
    source_label: str
    path: str
    name: str
    ext: str = ""
    is_dir: bool
    status: str
    annotations: list[AnnotationOut] = []
    # Fremde Annotationen, die neuer sind als mein letzter Besuch der Quelle.
    has_new: bool = False


class SearchAssistIn(BaseModel):
    """Frage in Alltagssprache + das LLM-Setting, das sie übersetzen soll."""
    question: str = Field(min_length=1, max_length=1000)
    setting_id: int
    # Optionale eigene Vorlage mit zusätzlichen Hinweisen ans Modell.
    prompt_id: int | None = None
    limit: int = Field(default=50, le=200)


class SearchFiltersOut(BaseModel):
    """Wie der Assistent die Frage verstanden hat – im UI nachvollziehbar."""
    query: str = ""
    source_id: int | None = None
    source_label: str | None = None
    status: str | None = None
    ext: list[str] = []
    modified_after: date | None = None
    modified_before: date | None = None
    min_size: int | None = None
    max_size: int | None = None
    is_dir: bool | None = None


class SearchAssistOut(BaseModel):
    filters: SearchFiltersOut
    explanation: str = ""
    hits: list[SearchHit] = []
