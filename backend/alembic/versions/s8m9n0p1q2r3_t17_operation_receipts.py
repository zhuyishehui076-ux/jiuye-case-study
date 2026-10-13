"""T17 replay receipts for compound management writes.

Revision ID: s8m9n0p1q2r3
Revises: r7l8m9n0p1q2
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "s8m9n0p1q2r3"
down_revision = "r7l8m9n0p1q2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namespace", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.UniqueConstraint(
            "namespace",
            "idempotency_key",
            name="uq_operation_receipts_namespace_key",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_operation_receipts_uuid4_key",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_operation_receipts_request_hash",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(
        sa.text("LOCK TABLE operation_receipts IN ACCESS EXCLUSIVE MODE")
    )
    has_receipts = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM operation_receipts LIMIT 1)")
    ).scalar_one()
    if has_receipts:
        raise RuntimeError(
            "refusing to drop operation_receipts while replay evidence exists"
        )
    op.drop_table("operation_receipts")
