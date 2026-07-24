"""LLM-Integration: Verbindungen, Settings, Prompts, Feature-Zuordnung und
generischer Lauf-Speicher (``ai_runs``).

Alles nutzerbezogen (``owner_user_id`` -> users, CASCADE). Der Block ist
feature-unabhängig; die Notizen-Anbindung nutzt ihn nur als ersten Konsumenten.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _owner_col() -> sa.Column:
    return sa.Column(
        "owner_user_id",
        sa.Integer(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "llm_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        _owner_col(),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("provider_type", sa.String(30), nullable=False, server_default="openai"),
        sa.Column("base_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(200), nullable=False, server_default=""),
        sa.Column("extra_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("models_cache_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_llm_connections_owner_user_id", "llm_connections", ["owner_user_id"]
    )

    op.create_table(
        "llm_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        _owner_col(),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("llm_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("params_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_llm_settings_owner_user_id", "llm_settings", ["owner_user_id"]
    )
    op.create_index(
        "ix_llm_settings_connection_id", "llm_settings", ["connection_id"]
    )

    op.create_table(
        "llm_prompts",
        sa.Column("id", sa.Integer(), primary_key=True),
        _owner_col(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_llm_prompts_owner_user_id", "llm_prompts", ["owner_user_id"]
    )

    op.create_table(
        "llm_feature_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        _owner_col(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=False),
        sa.Column("feature_key", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "kind", "ref_id", "feature_key", name="uq_llm_feature_link"
        ),
    )
    op.create_index(
        "ix_llm_feature_links_owner_user_id", "llm_feature_links", ["owner_user_id"]
    )
    op.create_index("ix_llm_feature_links_ref_id", "llm_feature_links", ["ref_id"])
    op.create_index(
        "ix_llm_feature_links_feature_key", "llm_feature_links", ["feature_key"]
    )

    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        _owner_col(),
        sa.Column("target_kind", sa.String(30), nullable=False, server_default=""),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("llm_settings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "prompt_id",
            sa.Integer(),
            sa.ForeignKey("llm_prompts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("input_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("meta_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_runs_owner_user_id", "ai_runs", ["owner_user_id"])
    op.create_index("ix_ai_runs_target_kind", "ai_runs", ["target_kind"])
    op.create_index("ix_ai_runs_target_id", "ai_runs", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_runs_target_id", table_name="ai_runs")
    op.drop_index("ix_ai_runs_target_kind", table_name="ai_runs")
    op.drop_index("ix_ai_runs_owner_user_id", table_name="ai_runs")
    op.drop_table("ai_runs")

    op.drop_index(
        "ix_llm_feature_links_feature_key", table_name="llm_feature_links"
    )
    op.drop_index("ix_llm_feature_links_ref_id", table_name="llm_feature_links")
    op.drop_index(
        "ix_llm_feature_links_owner_user_id", table_name="llm_feature_links"
    )
    op.drop_table("llm_feature_links")

    op.drop_index("ix_llm_prompts_owner_user_id", table_name="llm_prompts")
    op.drop_table("llm_prompts")

    op.drop_index("ix_llm_settings_connection_id", table_name="llm_settings")
    op.drop_index("ix_llm_settings_owner_user_id", table_name="llm_settings")
    op.drop_table("llm_settings")

    op.drop_index("ix_llm_connections_owner_user_id", table_name="llm_connections")
    op.drop_table("llm_connections")
