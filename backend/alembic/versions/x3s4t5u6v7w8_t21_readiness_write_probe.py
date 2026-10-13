"""T21 reversible readiness write probe.

Revision ID: x3s4t5u6v7w8
Revises: w2r3s4t5u6v7
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "x3s4t5u6v7w8"
down_revision = "w2r3s4t5u6v7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_readiness_probes",
        sa.Column(
            "probe_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    # Readiness is exercised frequently. Failed/rolled-back probe tuples must
    # be reclaimed promptly without waiting for business-table thresholds.
    op.execute(
        "ALTER TABLE ops_readiness_probes SET ("
        "autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 50)"
    )


def downgrade() -> None:
    op.drop_table("ops_readiness_probes")
