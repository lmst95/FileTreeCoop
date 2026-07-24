"""Repariert den Unique-Constraint auf ``source_shares``.

Auf Datenbanken, die den alten Vor-Alembic-Migrationspfad durchlaufen haben
(``_migrate()`` in ``app/db.py`` + Stempel auf "0001"), wurde die Spalte
``path_prefix`` per ``ALTER TABLE ... ADD COLUMN`` nachgerüstet, aber der
ursprüngliche Unique-Index ``uq_share`` blieb bei ``(source_id, user_id)``
stehen (SQLite passt bestehende Indizes bei ADD COLUMN nicht an). Dadurch
schlug das Teilen eines zweiten Teilbaums derselben Quelle an denselben
Nutzer mit einem UNIQUE-Constraint-Fehler fehl, obwohl das Modell
``(source_id, user_id, path_prefix)`` vorsieht.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-23
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_shares") as batch:
        batch.drop_constraint("uq_share", type_="unique")
        batch.create_unique_constraint(
            "uq_share", ["source_id", "user_id", "path_prefix"]
        )


def downgrade() -> None:
    with op.batch_alter_table("source_shares") as batch:
        batch.drop_constraint("uq_share", type_="unique")
        batch.create_unique_constraint("uq_share", ["source_id", "user_id"])
