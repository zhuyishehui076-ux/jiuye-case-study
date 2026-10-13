"""T17 make scene metric and company visit appends replay-safe.

Revision ID: r7l8m9n0p1q2
Revises: q6k7l8m9n0p1
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r7l8m9n0p1q2"
down_revision = "q6k7l8m9n0p1"
branch_labels = None
depends_on = None


def _add_operation_columns(
    table: str,
    *,
    unique_name: str,
    check_prefix: str,
) -> None:
    op.add_column(
        table,
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        table,
        sa.Column("request_hash", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(unique_name, table, ["idempotency_key"])
    op.create_check_constraint(
        f"ck_{check_prefix}_operation_pair",
        table,
        "(idempotency_key IS NULL AND request_hash IS NULL) OR "
        "(idempotency_key IS NOT NULL AND request_hash IS NOT NULL)",
    )
    op.create_check_constraint(
        f"ck_{check_prefix}_idempotency_key_length",
        table,
        "idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        f"ck_{check_prefix}_request_hash",
        table,
        "request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'",
    )


def upgrade() -> None:
    _add_operation_columns(
        "scene_metrics",
        unique_name="uq_scene_metrics_idempotency_key",
        check_prefix="scene_metrics",
    )
    _add_operation_columns(
        "company_visits",
        unique_name="uq_company_visits_idempotency_key",
        check_prefix="company_visits",
    )


def _drop_operation_columns(
    table: str,
    *,
    unique_name: str,
    check_prefix: str,
) -> None:
    op.drop_constraint(f"ck_{check_prefix}_request_hash", table, type_="check")
    op.drop_constraint(
        f"ck_{check_prefix}_idempotency_key_length",
        table,
        type_="check",
    )
    op.drop_constraint(f"ck_{check_prefix}_operation_pair", table, type_="check")
    op.drop_constraint(unique_name, table, type_="unique")
    op.drop_column(table, "request_hash")
    op.drop_column(table, "idempotency_key")


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(
        sa.text(
            "LOCK TABLE company_visits, scene_metrics "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    for table in ("company_visits", "scene_metrics"):
        protected = int(
            connection.scalar(
                sa.text(
                    f"SELECT count(*) FROM {table} "
                    "WHERE idempotency_key IS NOT NULL"
                )
            )
            or 0
        )
        if protected:
            raise RuntimeError(
                "T17 downgrade blocked: replay-safe management writes exist "
                f"(table={table}, protected_rows={protected})."
            )
    _drop_operation_columns(
        "company_visits",
        unique_name="uq_company_visits_idempotency_key",
        check_prefix="company_visits",
    )
    _drop_operation_columns(
        "scene_metrics",
        unique_name="uq_scene_metrics_idempotency_key",
        check_prefix="scene_metrics",
    )
