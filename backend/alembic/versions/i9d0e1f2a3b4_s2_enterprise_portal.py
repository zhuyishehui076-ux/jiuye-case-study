"""s2 enterprise access token and owned job submissions

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i9d0e1f2a3b4"
down_revision: str | None = "h8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    op.create_table(
        "enterprise_access_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.String(length=128), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("submit_window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_enterprise_access_tokens_digest"),
    )
    op.create_table(
        "enterprise_job_submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("access_token_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["access_token_id"],
            ["enterprise_access_tokens.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_enterprise_job_submissions_job"),
    )
    op.create_index(
        "ix_enterprise_job_submissions_token_status",
        "enterprise_job_submissions",
        ["access_token_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enterprise_job_submissions_token_status",
        table_name="enterprise_job_submissions",
    )
    op.drop_table("enterprise_job_submissions")
    op.drop_table("enterprise_access_tokens")
