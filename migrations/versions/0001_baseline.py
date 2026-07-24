"""Baseline: Schema vor dem Kooperations-/Versionierungs-Ausbau.

Bestehende Datenbanken werden auf diese Revision *gestempelt* (nicht
migriert) – der Upgrade-Code hier läuft nur, wenn eine leere DB per
``alembic upgrade`` statt über ``init_db()`` aufgebaut wird.

Revision ID: 0001
Revises:
Create Date: 2026-07-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("host_hint", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_scanned_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sources_owner_user_id", "sources", ["owner_user_id"])

    op.create_table(
        "source_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path_prefix", sa.Text(), nullable=False),
        sa.Column("permission", sa.String(20), nullable=False),
        sa.UniqueConstraint("source_id", "user_id", "path_prefix", name="uq_share"),
    )
    op.create_index("ix_source_shares_source_id", "source_shares", ["source_id"])
    op.create_index("ix_source_shares_user_id", "source_shares", ["user_id"])

    op.create_table(
        "entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("ext", sa.String(50), nullable=False),
        sa.Column("is_dir", sa.Boolean(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("mtime", sa.Float(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("source_id", "path", name="uq_entry_path"),
    )
    op.create_index("ix_entries_source_id", "entries", ["source_id"])
    op.create_index("ix_entries_name", "entries", ["name"])

    op.create_table(
        "annotations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("label_value", sa.String(120), nullable=False),
        sa.Column(
            "assignee_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_annotations_entry_id", "annotations", ["entry_id"])
    op.create_index("ix_annotations_author_user_id", "annotations", ["author_user_id"])
    op.create_index("ix_annotations_due_date", "annotations", ["due_date"])


def downgrade() -> None:
    op.drop_table("annotations")
    op.drop_table("entries")
    op.drop_table("source_shares")
    op.drop_table("sources")
    op.drop_table("users")
