"""T20 authentication throttling and revocable management sessions.

Revision ID: w2r3s4t5u6v7
Revises: v1q2r3s4t5u6
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "w2r3s4t5u6v7"
down_revision = "v1q2r3s4t5u6"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
    ]


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "management_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("jti_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=128), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_management_sessions_user_id_users",
        ),
        sa.UniqueConstraint(
            "jti_digest", name="uq_management_sessions_jti_digest"
        ),
        sa.CheckConstraint(
            "jti_digest ~ '^[0-9a-f]{64}$'",
            name="ck_management_sessions_jti_digest",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_management_sessions_expiry",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name="ck_management_sessions_revocation",
        ),
    )
    op.create_index(
        "ix_management_sessions_user_expires",
        "management_sessions",
        ["user_id", "expires_at"],
    )
    op.create_index(
        "ix_management_sessions_user_revoked",
        "management_sessions",
        ["user_id", "revoked_at"],
    )

    op.create_table(
        "auth_throttles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("principal_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "failure_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "surface",
            "principal_digest",
            name="uq_auth_throttles_surface_principal",
        ),
        sa.CheckConstraint(
            "principal_digest ~ '^[0-9a-f]{64}$'",
            name="ck_auth_throttles_principal_digest",
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name="ck_auth_throttles_failure_count",
        ),
        sa.CheckConstraint(
            "locked_until IS NULL OR locked_until >= window_started_at",
            name="ck_auth_throttles_lock_window",
        ),
    )
    op.create_index(
        "ix_auth_throttles_locked_until",
        "auth_throttles",
        ["locked_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_throttles_locked_until", table_name="auth_throttles")
    op.drop_table("auth_throttles")
    op.drop_index(
        "ix_management_sessions_user_revoked", table_name="management_sessions"
    )
    op.drop_index(
        "ix_management_sessions_user_expires", table_name="management_sessions"
    )
    op.drop_table("management_sessions")
    op.drop_column("users", "password_changed_at")
