"""t2 job_parses 人工复核队列列

Revision ID: a1b2c3d4e5f6
Revises: 55a2c75a5556
Create Date: 2026-07-06 09:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "55a2c75a5556"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_parses",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("job_parses", sa.Column("review_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_parses", "review_reason")
    op.drop_column("job_parses", "needs_review")
