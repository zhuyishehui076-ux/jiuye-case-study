"""t14 fair type for same-category comparison

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fairs",
        sa.Column(
            "fair_type",
            sa.String(length=32),
            server_default="综合招聘会",
            nullable=False,
        ),
    )
    op.create_index("ix_fairs_fair_type", "fairs", ["fair_type"])


def downgrade() -> None:
    op.drop_index("ix_fairs_fair_type", table_name="fairs")
    op.drop_column("fairs", "fair_type")
