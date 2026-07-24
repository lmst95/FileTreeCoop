"""Freie Notizen (Pinnwand): ``annotations.entry_id`` wird nullbar (None =
Notiz ohne Datei-Bezug, nur bei type == "note"), dazu eine Pinnwand-Farbe
(``color``) und eine eigene Freigabe-Tabelle für freie Notizen.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("annotations") as batch:
        batch.alter_column("entry_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(
            sa.Column("color", sa.String(20), nullable=False, server_default="")
        )

    op.create_table(
        "annotation_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "annotation_id",
            sa.Integer(),
            sa.ForeignKey("annotations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("annotation_id", "user_id", name="uq_annotation_share"),
    )
    op.create_index(
        "ix_annotation_shares_annotation_id", "annotation_shares", ["annotation_id"]
    )
    op.create_index("ix_annotation_shares_user_id", "annotation_shares", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_annotation_shares_user_id", table_name="annotation_shares")
    op.drop_index(
        "ix_annotation_shares_annotation_id", table_name="annotation_shares"
    )
    op.drop_table("annotation_shares")
    with op.batch_alter_table("annotations") as batch:
        batch.drop_column("color")
        batch.alter_column("entry_id", existing_type=sa.Integer(), nullable=False)
