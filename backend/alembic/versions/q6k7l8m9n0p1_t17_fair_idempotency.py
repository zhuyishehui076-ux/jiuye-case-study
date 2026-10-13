"""T17 make fair creation and enterprise registration replay-safe.

Revision ID: q6k7l8m9n0p1
Revises: p5j6k7l8m9n0
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "q6k7l8m9n0p1"
down_revision = "p5j6k7l8m9n0"
branch_labels = None
depends_on = None


def _add_operation_columns(table: str, *, unique_name: str, check_prefix: str) -> None:
    op.add_column(table, sa.Column("idempotency_key", sa.String(length=96), nullable=True))
    op.add_column(table, sa.Column("request_hash", sa.String(length=64), nullable=True))
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
        "idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 8 AND 96",
    )
    op.create_check_constraint(
        f"ck_{check_prefix}_request_hash",
        table,
        "request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'",
    )


def upgrade() -> None:
    _add_operation_columns(
        "fairs",
        unique_name="uq_fairs_idempotency_key",
        check_prefix="fairs",
    )
    _add_operation_columns(
        "fair_companies",
        unique_name="uq_fair_companies_idempotency_key",
        check_prefix="fair_companies",
    )


def _drop_operation_columns(table: str, *, unique_name: str, check_prefix: str) -> None:
    op.drop_constraint(f"ck_{check_prefix}_request_hash", table, type_="check")
    op.drop_constraint(
        f"ck_{check_prefix}_idempotency_key_length", table, type_="check"
    )
    op.drop_constraint(f"ck_{check_prefix}_operation_pair", table, type_="check")
    op.drop_constraint(unique_name, table, type_="unique")
    op.drop_column(table, "request_hash")
    op.drop_column(table, "idempotency_key")


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(
        sa.text("LOCK TABLE fair_companies, fairs IN ACCESS EXCLUSIVE MODE")
    )
    for table in ("fair_companies", "fairs"):
        protected = int(
            connection.scalar(
                sa.text(f"SELECT count(*) FROM {table} WHERE idempotency_key IS NOT NULL")
            )
            or 0
        )
        if protected:
            raise RuntimeError(
                "T17 downgrade blocked: replay-safe fair operations exist "
                f"(table={table}, protected_rows={protected})."
            )
    _drop_operation_columns(
        "fair_companies",
        unique_name="uq_fair_companies_idempotency_key",
        check_prefix="fair_companies",
    )
    _drop_operation_columns(
        "fairs",
        unique_name="uq_fairs_idempotency_key",
        check_prefix="fairs",
    )
