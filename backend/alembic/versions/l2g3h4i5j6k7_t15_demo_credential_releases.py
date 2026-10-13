"""T15 recoverable Demo credential releases and JWT credential versions.

Revision ID: l2g3h4i5j6k7
Revises: k1f2a3b4c5d6
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "l2g3h4i5j6k7"
down_revision = "k1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "credential_version", sa.Integer(), server_default="1", nullable=False
        ),
    )
    op.add_column(
        "student_bindings",
        sa.Column(
            "credential_version", sa.Integer(), server_default="1", nullable=False
        ),
    )
    op.create_table(
        "demo_credential_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("release_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("installation_id", sa.String(length=128), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("handoff_fingerprint", sa.String(length=64)),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "management_password_hashes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "verification_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "previous_release_id",
            sa.String(length=64),
            sa.ForeignKey("demo_credential_releases.release_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("student_binding_code_digest", sa.String(length=64), nullable=False),
        sa.Column("student_binding_code_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "enterprise_access_token_id",
            sa.Integer(),
            sa.ForeignKey("enterprise_access_tokens.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
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
        sa.Column("created_by", sa.String(length=64)),
        sa.CheckConstraint(
            "status IN ('staged', 'verified', 'promoting', 'active', "
            "'superseded', 'aborted')",
            name="ck_demo_credential_releases_status",
        ),
    )
    op.create_index(
        "uq_demo_credential_releases_candidate",
        "demo_credential_releases",
        ["installation_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('staged', 'verified', 'promoting')"
        ),
    )
    op.create_index(
        "uq_demo_credential_releases_active",
        "demo_credential_releases",
        ["installation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_demo_credential_releases_active",
        table_name="demo_credential_releases",
    )
    op.drop_index(
        "uq_demo_credential_releases_candidate",
        table_name="demo_credential_releases",
    )
    op.drop_table("demo_credential_releases")
    op.drop_column("student_bindings", "credential_version")
    op.drop_column("users", "credential_version")
