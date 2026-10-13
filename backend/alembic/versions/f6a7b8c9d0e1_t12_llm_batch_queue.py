"""t12 llm batch queue

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-11 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_batch_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("term", sa.String(length=16), nullable=True),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("completed_items", sa.Integer(), nullable=False),
        sa.Column("failed_items", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(length=32), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_batch_tasks_status", "llm_batch_tasks", ["status"])
    op.create_index("ix_llm_batch_tasks_task_type", "llm_batch_tasks", ["task_type"])
    op.create_table(
        "llm_batch_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["llm_batch_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "target_id", name="uq_llm_batch_item_task_target"),
    )
    op.create_index("ix_llm_batch_items_status", "llm_batch_items", ["status"])
    op.create_index("ix_llm_batch_items_target_id", "llm_batch_items", ["target_id"])
    op.create_index("ix_llm_batch_items_task_id", "llm_batch_items", ["task_id"])
    op.create_index(
        "ix_llm_batch_item_target_status",
        "llm_batch_items",
        ["target_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_batch_item_target_status", table_name="llm_batch_items")
    op.drop_index("ix_llm_batch_items_task_id", table_name="llm_batch_items")
    op.drop_index("ix_llm_batch_items_target_id", table_name="llm_batch_items")
    op.drop_index("ix_llm_batch_items_status", table_name="llm_batch_items")
    op.drop_table("llm_batch_items")
    op.drop_index("ix_llm_batch_tasks_task_type", table_name="llm_batch_tasks")
    op.drop_index("ix_llm_batch_tasks_status", table_name="llm_batch_tasks")
    op.drop_table("llm_batch_tasks")
