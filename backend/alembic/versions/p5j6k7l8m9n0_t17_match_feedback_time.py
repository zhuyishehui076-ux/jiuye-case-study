"""T17 preserve the semantic time of a student match feedback fact.

Revision ID: p5j6k7l8m9n0
Revises: n4i5j6k7l8m9
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "p5j6k7l8m9n0"
down_revision = "n4i5j6k7l8m9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True),
    )
    # For historical rows the generic row-update time is the only timestamp
    # that survives. Freeze it once as the semantic feedback time so later
    # recommendation recalculation can no longer rewrite the journey event.
    op.execute(
        sa.text(
            "UPDATE matches SET feedback_at = updated_at "
            "WHERE feedback IS NOT NULL"
        )
    )
    op.create_check_constraint(
        "ck_matches_feedback_timestamp_pair",
        "matches",
        "(feedback IS NULL AND feedback_at IS NULL) OR "
        "(feedback IS NOT NULL AND feedback_at IS NOT NULL)",
    )


def downgrade() -> None:
    # Preserve the best available business time in the legacy fallback column
    # before removing the dedicated field.
    op.execute(
        sa.text(
            "UPDATE matches SET updated_at = feedback_at "
            "WHERE feedback_at IS NOT NULL"
        )
    )
    op.drop_constraint(
        "ck_matches_feedback_timestamp_pair",
        "matches",
        type_="check",
    )
    op.drop_column("matches", "feedback_at")
