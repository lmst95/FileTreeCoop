"""„Verschwunden“-Erkennung auf Scan-Zugehörigkeit statt Uhrzeit-Vergleich
umstellen: neue Spalte ``entries.last_scan_id`` hält den zuletzt sehenden
Scan-Lauf fest. Der alte Vergleich per ``last_seen < scan.started_at`` konnte
bei schnell aufeinanderfolgenden Scans durch Uhr-Auflösung ins Wanken geraten.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("entries") as batch:
        batch.add_column(
            sa.Column(
                "last_scan_id",
                sa.Integer(),
                sa.ForeignKey(
                    "scans.id", ondelete="SET NULL", name="fk_entries_last_scan_id"
                ),
                nullable=True,
            )
        )
    op.create_index("ix_entries_last_scan_id", "entries", ["last_scan_id"])


def downgrade() -> None:
    op.drop_index("ix_entries_last_scan_id", table_name="entries")
    with op.batch_alter_table("entries") as batch:
        batch.drop_column("last_scan_id")
