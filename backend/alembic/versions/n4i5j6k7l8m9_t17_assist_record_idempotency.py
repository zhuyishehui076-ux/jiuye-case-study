"""T17 persist assist-record idempotency metadata.

Revision ID: n4i5j6k7l8m9
Revises: m3h4i5j6k7l8
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "n4i5j6k7l8m9"
down_revision = "m3h4i5j6k7l8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assist_records",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "assist_records",
        sa.Column("request_hash", sa.String(length=64), nullable=True),
    )

    # Existing append-only facts predate the public idempotency contract. Give
    # them a reserved key that cannot pass the API's UUIDv4 validator, so they
    # stay immutable and can never be mistaken for a replayable client write.
    op.execute(
        sa.text(
            "UPDATE assist_records "
            "SET idempotency_key = 'legacy-assist-record-' || id::text, "
            "request_hash = repeat('0', 64)"
        )
    )
    op.alter_column("assist_records", "idempotency_key", nullable=False)
    op.alter_column("assist_records", "request_hash", nullable=False)
    op.create_unique_constraint(
        "uq_assist_records_idempotency_key",
        "assist_records",
        ["idempotency_key"],
    )
    op.create_check_constraint(
        "ck_assist_records_idempotency_key_length",
        "assist_records",
        "char_length(idempotency_key) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        "ck_assist_records_request_hash",
        "assist_records",
        "request_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(
        sa.text("LOCK TABLE assist_records IN ACCESS EXCLUSIVE MODE")
    )
    protected_rows = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM assist_records "
                "WHERE idempotency_key !~ '^legacy-assist-record-[0-9]+$'"
            )
        )
        or 0
    )
    if protected_rows:
        raise RuntimeError(
            "T17 downgrade blocked: assist records using the idempotency contract exist "
            f"(protected_rows={protected_rows}). Preserve or migrate those facts before "
            "removing replay metadata."
        )

    op.drop_constraint(
        "ck_assist_records_request_hash",
        "assist_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_assist_records_idempotency_key_length",
        "assist_records",
        type_="check",
    )
    op.drop_constraint(
        "uq_assist_records_idempotency_key",
        "assist_records",
        type_="unique",
    )
    op.drop_column("assist_records", "request_hash")
    op.drop_column("assist_records", "idempotency_key")
