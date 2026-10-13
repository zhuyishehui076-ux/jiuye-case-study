"""t8 fair company jd link

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-10 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("fair_companies", sa.Column("title", sa.String(length=128), nullable=True))
    op.add_column("fair_companies", sa.Column("raw_text", sa.Text(), nullable=True))
    op.add_column("fair_companies", sa.Column("job_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_fair_companies_job_id_jobs",
        "fair_companies",
        "jobs",
        ["job_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_fair_companies_job_id_jobs", "fair_companies", type_="foreignkey")
    op.drop_column("fair_companies", "job_id")
    op.drop_column("fair_companies", "raw_text")
    op.drop_column("fair_companies", "title")
