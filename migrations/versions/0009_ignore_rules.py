"""Ignorierregeln: dauerhaft aus der Suche ausgeblendete Ordner/Dateinamen.

Eine Regel gehört einem Nutzer, gilt für alle Quellen (``source_id IS NULL``)
oder nur eine, und blendet entweder einen Pfad samt Unterbaum (``kind='path'``)
oder passende Dateinamen (``kind='name'``) aus. ``active`` schaltet sie ab, ohne
sie zu verlieren.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ignore_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(10), nullable=False, server_default="path"),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ignore_rules_user_id", "ignore_rules", ["user_id"])
    op.create_index("ix_ignore_rules_source_id", "ignore_rules", ["source_id"])
    op.create_index(
        "ix_ignore_rules_user_active", "ignore_rules", ["user_id", "active"]
    )


def downgrade() -> None:
    op.drop_index("ix_ignore_rules_user_active", table_name="ignore_rules")
    op.drop_index("ix_ignore_rules_source_id", table_name="ignore_rules")
    op.drop_index("ix_ignore_rules_user_id", table_name="ignore_rules")
    op.drop_table("ignore_rules")
