"""Übersprungene (nicht erreichbare) Einträge eines Scans persistieren.

Neuer Zähler ``scans.skipped`` sowie Tabelle ``scan_skips`` mit den Pfaden,
damit das UI auch nach einem Reload zeigen kann, was beim Scan eines
Netzlaufwerks nicht erfasst werden konnte.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scans") as batch:
        batch.add_column(
            sa.Column("skipped", sa.Integer(), nullable=False, server_default="0")
        )

    op.create_table(
        "scan_skips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scan_skips_scan_id", "scan_skips", ["scan_id"])


def downgrade() -> None:
    op.drop_index("ix_scan_skips_scan_id", table_name="scan_skips")
    op.drop_table("scan_skips")
    with op.batch_alter_table("scans") as batch:
        batch.drop_column("skipped")
