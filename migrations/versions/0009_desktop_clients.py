"""Desktop-Client: registrierte Geräte, überwachte Ordner, Befehls-Queue.

``clients`` sind die registrierten Hintergrund-Agenten eines Nutzers (Auth per
gehashtem Gerätetoken), ``client_folders`` die von ihnen überwachten Ordner je
Quelle, ``client_commands`` die Aufträge, die ein Client beim Heartbeat abholt
(z. B. „öffne diesen Ordner im Explorer“).

Dazu bekommt ``scans`` eine Spalte ``kind``: der Client schickt aus der
Live-Überwachung viele kleine Deltas, die als ``live`` markiert werden und
deshalb Dashboard-Diff und Aktivitäts-Feed nicht zumüllen.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("kind", sa.String(10), nullable=False, server_default="full"),
    )

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("hostname", sa.String(200), nullable=False, server_default=""),
        sa.Column("platform", sa.String(40), nullable=False, server_default=""),
        sa.Column("version", sa.String(40), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("status_text", sa.String(300), nullable=False, server_default=""),
        sa.Column(
            "paused", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_index("ix_clients_owner_user_id", "clients", ["owner_user_id"])
    op.create_index("ux_clients_token_hash", "clients", ["token_hash"], unique=True)

    op.create_table(
        "client_folders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_path", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "hash_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "watch_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "scan_interval_minutes", sa.Integer(), nullable=False, server_default="60"
        ),
        sa.Column("last_scan_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=False, server_default=""),
        sa.UniqueConstraint("client_id", "source_id", name="uq_client_folder"),
    )
    op.create_index("ix_client_folders_client_id", "client_folders", ["client_id"])
    op.create_index("ix_client_folders_source_id", "client_folders", ["source_id"])

    op.create_table(
        "client_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command", sa.String(30), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("result", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_client_commands_client_id", "client_commands", ["client_id"])
    op.create_index("ix_client_commands_status", "client_commands", ["status"])


def downgrade() -> None:
    op.drop_index("ix_client_commands_status", table_name="client_commands")
    op.drop_index("ix_client_commands_client_id", table_name="client_commands")
    op.drop_table("client_commands")
    op.drop_index("ix_client_folders_source_id", table_name="client_folders")
    op.drop_index("ix_client_folders_client_id", table_name="client_folders")
    op.drop_table("client_folders")
    op.drop_index("ux_clients_token_hash", table_name="clients")
    op.drop_index("ix_clients_owner_user_id", table_name="clients")
    op.drop_table("clients")
    op.drop_column("scans", "kind")
