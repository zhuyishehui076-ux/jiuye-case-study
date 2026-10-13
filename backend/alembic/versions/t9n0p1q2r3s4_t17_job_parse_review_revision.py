"""T17 protect job-parse reviews from stale browser overwrites.

Revision ID: t9n0p1q2r3s4
Revises: s8m9n0p1q2r3
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "t9n0p1q2r3s4"
down_revision = "s8m9n0p1q2r3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_parses",
        sa.Column(
            "review_revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_job_parses_review_revision_positive",
        "job_parses",
        "review_revision >= 1",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    # The preceding revision refuses to discard committed operation receipts.
    # Stop at the current head as well, otherwise a multi-revision downgrade
    # could first erase the CAS column and only then fail on those receipts.
    connection.execute(
        sa.text("LOCK TABLE operation_receipts IN ACCESS EXCLUSIVE MODE")
    )
    connection.execute(sa.text("LOCK TABLE job_parses IN ACCESS EXCLUSIVE MODE"))
    receipt_count = int(
        connection.scalar(sa.text("SELECT count(*) FROM operation_receipts")) or 0
    )
    revised_count = int(
        connection.scalar(
            sa.text("SELECT count(*) FROM job_parses WHERE review_revision > 1")
        )
        or 0
    )
    if receipt_count or revised_count:
        raise RuntimeError(
            "refusing to drop job parse review revisions while replay or review "
            "evidence exists "
            f"(operation_receipts={receipt_count}, revised_parses={revised_count})"
        )
    op.drop_constraint(
        "ck_job_parses_review_revision_positive",
        "job_parses",
        type_="check",
    )
    op.drop_column("job_parses", "review_revision")
