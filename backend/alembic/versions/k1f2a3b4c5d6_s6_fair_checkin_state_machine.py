"""S6 fair five-state check-in, enterprise check-in and frozen snapshots.

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "k1f2a3b4c5d6"
down_revision = "j0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fairs",
        sa.Column("status", sa.String(length=16), server_default="已归档", nullable=False),
    )
    op.add_column("fairs", sa.Column("registration_opens_at", sa.DateTime(timezone=True)))
    op.add_column("fairs", sa.Column("registration_closes_at", sa.DateTime(timezone=True)))
    op.add_column("fairs", sa.Column("checkin_started_at", sa.DateTime(timezone=True)))
    op.add_column("fairs", sa.Column("checkin_ended_at", sa.DateTime(timezone=True)))
    op.add_column("fairs", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_index("ix_fairs_status", "fairs", ["status"])

    op.add_column(
        "fair_checkins",
        sa.Column("source", sa.String(length=16), server_default="校方登记", nullable=False),
    )
    op.add_column(
        "fair_checkins",
        sa.Column("idempotency_key", sa.String(length=96), nullable=True),
    )
    op.add_column("fair_checkins", sa.Column("corrected_by", sa.String(length=64)))
    op.add_column("fair_checkins", sa.Column("correction_reason", sa.Text()))
    op.add_column("fair_checkins", sa.Column("corrected_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint(
        "uq_fair_checkin_student", "fair_checkins", ["fair_id", "student_id"]
    )
    op.create_unique_constraint(
        "uq_fair_checkins_idempotency_key", "fair_checkins", ["idempotency_key"]
    )

    op.create_table(
        "fair_enterprise_checkins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fair_company_id",
            sa.Integer(),
            sa.ForeignKey("fair_companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("representative_name", sa.String(length=64), nullable=False),
        sa.Column("gate_id", sa.String(length=32)),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), unique=True),
        sa.Column("corrected_by", sa.String(length=64)),
        sa.Column("correction_reason", sa.Text()),
        sa.Column("corrected_at", sa.DateTime(timezone=True)),
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
        sa.UniqueConstraint(
            "fair_company_id",
            "representative_name",
            name="uq_fair_enterprise_representative",
        ),
    )
    op.create_index(
        "ix_fair_enterprise_checkins_fair_company_id",
        "fair_enterprise_checkins",
        ["fair_company_id"],
    )
    op.create_table(
        "fair_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fair_id",
            sa.Integer(),
            sa.ForeignKey("fairs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
    )


def downgrade() -> None:
    op.drop_table("fair_snapshots")
    op.drop_index(
        "ix_fair_enterprise_checkins_fair_company_id",
        table_name="fair_enterprise_checkins",
    )
    op.drop_table("fair_enterprise_checkins")
    op.drop_constraint("uq_fair_checkins_idempotency_key", "fair_checkins", type_="unique")
    op.drop_constraint("uq_fair_checkin_student", "fair_checkins", type_="unique")
    for column in (
        "corrected_at",
        "correction_reason",
        "corrected_by",
        "idempotency_key",
        "source",
    ):
        op.drop_column("fair_checkins", column)
    op.drop_index("ix_fairs_status", table_name="fairs")
    for column in (
        "archived_at",
        "checkin_ended_at",
        "checkin_started_at",
        "registration_closes_at",
        "registration_opens_at",
        "status",
    ):
        op.drop_column("fairs", column)
