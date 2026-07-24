"""Datenbank-Setup: Engine, Session, Schema-Init und FTS5-Volltextindex.

Der Volltextindex ``entries_fts`` ist eine FTS5-Tabelle mit rowid == entries.id
und den Spalten (name, path, notes). Sie wird per SQL-Triggern automatisch mit
``entries`` und ``annotations`` synchron gehalten, sodass die Suche immer aktuell
ist — egal ob über die App oder direkt via SQL geschrieben wird.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

engine: Engine = create_engine(
    settings.db_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """Foreign-Keys in SQLite aktivieren (per Verbindung nötig)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# --- FTS5-Index + Trigger ---------------------------------------------------

# Aggregiert alle Annotationstexte (body + label_value) eines Eintrags zu einem
# durchsuchbaren String.
_NOTES_SUBQUERY = (
    "SELECT COALESCE(GROUP_CONCAT(body || ' ' || label_value, ' '), '') "
    "FROM annotations WHERE entry_id = {eid}"
)

_FTS_SETUP = [
    # Standalone-FTS5-Tabelle; unicode61 mit Entfernen von Diakritika, damit
    # z. B. "Angebot" auch "Ängebot" o. ä. tolerant matcht.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
        name, path, notes,
        tokenize = "unicode61 remove_diacritics 2"
    )
    """,
    # entries -> INSERT
    """
    CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
        INSERT INTO entries_fts(rowid, name, path, notes)
        VALUES (NEW.id, NEW.name, NEW.path, '');
    END
    """,
    # entries -> UPDATE von name/path
    """
    CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE OF name, path ON entries BEGIN
        UPDATE entries_fts SET name = NEW.name, path = NEW.path WHERE rowid = NEW.id;
    END
    """,
    # entries -> DELETE
    """
    CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
        DELETE FROM entries_fts WHERE rowid = OLD.id;
    END
    """,
    # annotations -> INSERT: notes-Spalte des Eintrags neu berechnen
    f"""
    CREATE TRIGGER IF NOT EXISTS ann_ai AFTER INSERT ON annotations BEGIN
        UPDATE entries_fts
        SET notes = ({_NOTES_SUBQUERY.format(eid='NEW.entry_id')})
        WHERE rowid = NEW.entry_id;
    END
    """,
    # annotations -> UPDATE
    f"""
    CREATE TRIGGER IF NOT EXISTS ann_au AFTER UPDATE ON annotations BEGIN
        UPDATE entries_fts
        SET notes = ({_NOTES_SUBQUERY.format(eid='NEW.entry_id')})
        WHERE rowid = NEW.entry_id;
    END
    """,
    # annotations -> DELETE
    f"""
    CREATE TRIGGER IF NOT EXISTS ann_ad AFTER DELETE ON annotations BEGIN
        UPDATE entries_fts
        SET notes = ({_NOTES_SUBQUERY.format(eid='OLD.entry_id')})
        WHERE rowid = OLD.entry_id;
    END
    """,
]


def _slugify_username(raw: str) -> str:
    """Erzeugt aus einem String einen gültigen Basis-Usernamen."""
    import re

    slug = re.sub(r"[^a-z0-9._-]", "", (raw or "").lower())
    return slug or "user"


def _migrate(conn) -> None:
    """Kleine Vorwärts-Migrationen für bestehende Dev-Datenbanken."""
    share_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(source_shares)")}
    if "path_prefix" not in share_cols:
        conn.exec_driver_sql(
            "ALTER TABLE source_shares ADD COLUMN path_prefix TEXT NOT NULL DEFAULT ''"
        )

    ann_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(annotations)")}
    if ann_cols and "due_date" not in ann_cols:
        conn.exec_driver_sql("ALTER TABLE annotations ADD COLUMN due_date DATE")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_annotations_due_date "
            "ON annotations(due_date)"
        )

    user_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)")}
    if user_cols and "username" not in user_cols:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN username TEXT")
        # Bestehende Nutzer mit eindeutigem Usernamen aus der E-Mail befüllen.
        rows = list(conn.exec_driver_sql("SELECT id, email FROM users"))
        taken: set[str] = set()
        for uid, email in rows:
            base = _slugify_username((email or "").split("@")[0]) or f"user{uid}"
            name = base
            n = 1
            while name in taken:
                n += 1
                name = f"{base}{n}"
            taken.add(name)
            conn.exec_driver_sql(
                "UPDATE users SET username = ? WHERE id = ?", (name, uid)
            )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users(username)"
        )


def _alembic_config():
    """Alembic-Config programmatisch, mit DB-URL aus den App-Settings."""
    from alembic.config import Config as AlembicConfig

    from app.config import BASE_DIR

    cfg = AlembicConfig(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.db_url)
    return cfg


def init_db() -> None:
    """Bringt die Datenbank auf den aktuellen Stand (idempotent).

    Drei Fälle:
    - Frische DB           -> ORM-``create_all`` + Alembic-Stamp auf ``head``.
    - Vor-Alembic-Dev-DB   -> Legacy-``_migrate`` (hebt sie auf den
      Baseline-Stand 0001), Stamp auf 0001, dann ``upgrade head``.
    - Alembic-verwaltete DB-> ``upgrade head``.

    Der FTS5-Index samt Triggern wird immer (idempotent) sichergestellt.
    """
    from alembic import command
    from sqlalchemy import inspect

    insp = inspect(engine)
    has_users = insp.has_table("users")
    has_version = insp.has_table("alembic_version")
    cfg = _alembic_config()

    if not has_users:
        Base.metadata.create_all(bind=engine)
        command.stamp(cfg, "head")
    elif not has_version:
        with engine.begin() as conn:
            _migrate(conn)
        command.stamp(cfg, "0001")
        command.upgrade(cfg, "head")
    else:
        command.upgrade(cfg, "head")

    with engine.begin() as conn:
        for stmt in _FTS_SETUP:
            conn.execute(text(stmt))


def get_db() -> Iterator[Session]:
    """FastAPI-Dependency: liefert eine Session und schließt sie danach."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
