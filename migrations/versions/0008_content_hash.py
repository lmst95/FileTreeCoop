"""Inhalts-Hash je Eintrag (im Browser berechnet).

``content_hash`` hält SHA-256 als Hex, ``hash_state`` den Ausgang des Versuchs
(ok | skipped | error) und ``hash_size``/``hash_mtime`` den Dateistand, für den
der Hash gilt – daran erkennt der Server veraltete Hashes und fordert sie neu
an. Der Index über (source_id, content_hash) trägt die Duplikat-Suche.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entries",
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "entries",
        sa.Column("hash_state", sa.String(20), nullable=False, server_default=""),
    )
    op.add_column("entries", sa.Column("hash_size", sa.Integer(), nullable=True))
    op.add_column("entries", sa.Column("hash_mtime", sa.Float(), nullable=True))
    op.create_index(
        "ix_entries_source_content_hash", "entries", ["source_id", "content_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_entries_source_content_hash", table_name="entries")
    op.drop_column("entries", "hash_mtime")
    op.drop_column("entries", "hash_size")
    op.drop_column("entries", "hash_state")
    op.drop_column("entries", "content_hash")
