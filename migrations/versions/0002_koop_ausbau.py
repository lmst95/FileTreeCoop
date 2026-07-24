"""Kooperations-Ausbau: Scans, Änderungs-Historie, Besuche, Einladungen,
Threads/Status an Annotationen, Aktivitäts-Marker am Nutzer.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scan_uuid", sa.String(64), nullable=False),
        sa.Column(
            "started_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("initial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("moved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reappeared", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_scans_source_id", "scans", ["source_id"])
    op.create_index("ix_scans_scan_uuid", "scans", ["scan_uuid"], unique=True)

    op.create_table(
        "entry_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change", sa.String(20), nullable=False),
        sa.Column("old_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("old_size", sa.Integer(), nullable=True),
        sa.Column("new_size", sa.Integer(), nullable=True),
        sa.Column("old_mtime", sa.Float(), nullable=True),
        sa.Column("new_mtime", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_entry_changes_scan_id", "entry_changes", ["scan_id"])
    op.create_index("ix_entry_changes_entry_id", "entry_changes", ["entry_id"])

    op.create_table(
        "source_visits",
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
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "source_id", name="uq_source_visit"),
    )
    op.create_index("ix_source_visits_user_id", "source_visits", ["user_id"])
    op.create_index("ix_source_visits_source_id", "source_visits", ["source_id"])

    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path_prefix", sa.Text(), nullable=False, server_default=""),
        sa.Column("permission", sa.String(20), nullable=False, server_default="annotate"),
        sa.Column(
            "invited_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("email", "source_id", "path_prefix", name="uq_invite"),
    )
    op.create_index("ix_invites_email", "invites", ["email"])
    op.create_index("ix_invites_source_id", "invites", ["source_id"])

    with op.batch_alter_table("annotations") as batch:
        batch.add_column(sa.Column("parent_annotation_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("status", sa.String(20), nullable=False, server_default="open")
        )
        batch.create_foreign_key(
            "fk_annotations_parent",
            "annotations",
            ["parent_annotation_id"],
            ["id"],
            ondelete="CASCADE",
        )
    op.create_index(
        "ix_annotations_parent_annotation_id", "annotations", ["parent_annotation_id"]
    )

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_activity_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("last_activity_seen_at")
    op.drop_index("ix_annotations_parent_annotation_id", table_name="annotations")
    with op.batch_alter_table("annotations") as batch:
        batch.drop_constraint("fk_annotations_parent", type_="foreignkey")
        batch.drop_column("status")
        batch.drop_column("parent_annotation_id")
    op.drop_table("invites")
    op.drop_table("source_visits")
    op.drop_table("entry_changes")
    op.drop_table("scans")
