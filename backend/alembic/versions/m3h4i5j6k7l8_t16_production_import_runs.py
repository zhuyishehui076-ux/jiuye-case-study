"""T16 production import runs, academic classes, and batch lineage.

Revision ID: m3h4i5j6k7l8
Revises: l2g3h4i5j6k7
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "m3h4i5j6k7l8"
down_revision = "l2g3h4i5j6k7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("actor_role", sa.String(length=32)))
    op.add_column("audit_logs", sa.Column("source_channel", sa.String(length=64)))
    op.add_column("audit_logs", sa.Column("correlation_id", sa.String(length=64)))

    op.create_table(
        "academic_classes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("cohort_year", sa.Integer(), nullable=False),
        sa.Column("college_id", sa.Integer(), nullable=False),
        sa.Column("major_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64)),
        sa.ForeignKeyConstraint(
            ["college_id"],
            ["colleges.id"],
            name="fk_academic_classes_college_id_colleges",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["major_id"],
            ["majors.id"],
            name="fk_academic_classes_major_id_majors",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "college_id",
            "major_id",
            "cohort_year",
            "code",
            name="uq_academic_class_scope_code",
        ),
        sa.CheckConstraint(
            "cohort_year BETWEEN 2000 AND 2100",
            name="ck_academic_classes_cohort_year",
        ),
    )
    op.create_index(
        "ix_academic_classes_code",
        "academic_classes",
        ["code"],
    )
    op.create_index(
        "ix_academic_classes_cohort_year",
        "academic_classes",
        ["cohort_year"],
    )
    op.create_index(
        "ix_academic_classes_major_id",
        "academic_classes",
        ["major_id"],
    )

    op.add_column("students", sa.Column("class_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_students_class_id_academic_classes",
        "students",
        "academic_classes",
        ["class_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_students_class_id", "students", ["class_id"])

    op.create_table(
        "import_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cohort_year", sa.Integer(), nullable=False),
        sa.Column("business_note", sa.Text()),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="draft",
            nullable=False,
        ),
        sa.Column(
            "template_version",
            sa.String(length=16),
            server_default="t16.v1",
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "result_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confirmed_by", sa.String(length=64)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("rolled_back_by", sa.String(length=64)),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True)),
        sa.Column("rollback_reason", sa.String(length=512)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=64)),
        sa.CheckConstraint(
            "cohort_year BETWEEN 2000 AND 2100",
            name="ck_import_runs_cohort_year",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'needs_correction', 'ready', 'importing', "
            "'imported', 'rolling_back', 'rolled_back')",
            name="ck_import_runs_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_import_runs_revision"),
    )
    op.create_index("ix_import_runs_cohort_year", "import_runs", ["cohort_year"])
    op.create_index("ix_import_runs_status", "import_runs", ["status"])

    op.alter_column(
        "import_batches",
        "status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=24),
        existing_nullable=False,
        postgresql_using="status::varchar(24)",
    )
    op.add_column("import_batches", sa.Column("run_id", sa.Integer()))
    op.add_column(
        "import_batches",
        sa.Column("template_version", sa.String(length=16)),
    )
    op.add_column(
        "import_batches",
        sa.Column("source_sha256", sa.String(length=64)),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "source_rows",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "mapping_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column("correction_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "result_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column("supersedes_batch_id", sa.Integer()),
    )
    op.add_column(
        "import_batches",
        sa.Column("confirmed_by", sa.String(length=64)),
    )
    op.add_column(
        "import_batches",
        sa.Column("rolled_back_by", sa.String(length=64)),
    )
    op.create_foreign_key(
        "fk_import_batches_run_id_import_runs",
        "import_batches",
        "import_runs",
        ["run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_import_batches_supersedes_batch_id_import_batches",
        "import_batches",
        "import_batches",
        ["supersedes_batch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_import_batches_run_kind_sha256",
        "import_batches",
        ["run_id", "target_kind", "source_sha256"],
    )
    op.create_check_constraint(
        "ck_import_batches_status",
        "import_batches",
        "status IN ('validated', 'failed', 'draft', 'uploaded', "
        "'needs_correction', 'ready', 'importing', 'imported', "
        "'rolling_back', 'rolled_back', 'superseded')",
    )
    op.create_check_constraint(
        "ck_import_batches_row_count",
        "import_batches",
        "row_count >= 0",
    )
    op.create_check_constraint(
        "ck_import_batches_error_count",
        "import_batches",
        "error_count >= 0",
    )
    op.create_check_constraint(
        "ck_import_batches_correction_count",
        "import_batches",
        "correction_count >= 0",
    )
    op.create_check_constraint(
        "ck_import_batches_source_sha256",
        "import_batches",
        "source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_index("ix_import_batches_run_id", "import_batches", ["run_id"])
    op.create_index(
        "ix_import_batches_supersedes_batch_id",
        "import_batches",
        ["supersedes_batch_id"],
    )
    op.create_index(
        "ix_import_batches_run_kind_status",
        "import_batches",
        ["run_id", "target_kind", "status"],
    )

    op.create_table(
        "import_target_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("resource_kind", sa.String(length=24), nullable=False),
        sa.Column("field_scope", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batches.id"],
            name="fk_import_target_claims_batch_id_import_batches",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "resource_kind",
            "field_scope",
            "target_id",
            name="uq_import_target_claim_batch_scope_target",
        ),
        sa.UniqueConstraint(
            "resource_kind",
            "target_id",
            "target_version",
            name="uq_import_target_claim_target_version",
        ),
        sa.CheckConstraint(
            "resource_kind IN ('college', 'major', 'class', 'student')",
            name="ck_import_target_claim_resource_kind",
        ),
        sa.CheckConstraint(
            "field_scope IN ('record', 'student_profile', 'student_outcome')",
            name="ck_import_target_claim_field_scope",
        ),
        sa.CheckConstraint(
            "mode IN ('inserted', 'updated', 'unchanged')",
            name="ck_import_target_claim_mode",
        ),
        sa.CheckConstraint(
            "target_id > 0",
            name="ck_import_target_claim_target_id",
        ),
        sa.CheckConstraint(
            "target_version > 0",
            name="ck_import_target_claim_target_version",
        ),
    )
    op.create_index(
        "ix_import_target_claims_batch_id",
        "import_target_claims",
        ["batch_id"],
    )
    op.create_index(
        "ix_import_target_claim_active_target",
        "import_target_claims",
        ["resource_kind", "target_id", "released_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    # The emptiness check and destructive DDL must share one write barrier.
    # Otherwise an application write can land after the counts and be silently
    # discarded by the following DROP/ALTER statements.
    # Acquire the run table first, matching the application transaction entry
    # point, and fail within a bounded window instead of hanging an online
    # downgrade behind normal traffic.
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    for table_name in (
        "import_runs",
        "import_batches",
        "import_target_claims",
        "academic_classes",
        "students",
        "audit_logs",
    ):
        connection.execute(
            sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE")
        )
    protected_counts = {
        "import_runs": int(connection.scalar(sa.text("SELECT count(*) FROM import_runs")) or 0),
        "run_batches": int(
            connection.scalar(
                sa.text("SELECT count(*) FROM import_batches WHERE run_id IS NOT NULL")
            )
            or 0
        ),
        "target_claims": int(
            connection.scalar(sa.text("SELECT count(*) FROM import_target_claims")) or 0
        ),
        "academic_classes": int(
            connection.scalar(sa.text("SELECT count(*) FROM academic_classes")) or 0
        ),
        "assigned_students": int(
            connection.scalar(sa.text("SELECT count(*) FROM students WHERE class_id IS NOT NULL"))
            or 0
        ),
        "audit_trace_rows": int(
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_role IS NOT NULL "
                    "OR source_channel IS NOT NULL "
                    "OR correlation_id IS NOT NULL"
                )
            )
            or 0
        ),
    }
    if any(protected_counts.values()):
        summary = ", ".join(f"{key}={value}" for key, value in protected_counts.items())
        raise RuntimeError(
            "T16 downgrade blocked: production import lineage or academic-class data exists "
            f"({summary}). Export/rollback the affected runs and use an approved data migration "
            "before downgrading; this revision will not orphan or silently delete T16 facts."
        )

    op.drop_index(
        "ix_import_target_claim_active_target",
        table_name="import_target_claims",
    )
    op.drop_index(
        "ix_import_target_claims_batch_id",
        table_name="import_target_claims",
    )
    op.drop_table("import_target_claims")

    op.drop_index("ix_import_batches_run_kind_status", table_name="import_batches")
    op.drop_index(
        "ix_import_batches_supersedes_batch_id",
        table_name="import_batches",
    )
    op.drop_index("ix_import_batches_run_id", table_name="import_batches")
    op.drop_constraint(
        "ck_import_batches_source_sha256",
        "import_batches",
        type_="check",
    )
    op.drop_constraint(
        "ck_import_batches_correction_count",
        "import_batches",
        type_="check",
    )
    op.drop_constraint(
        "ck_import_batches_error_count",
        "import_batches",
        type_="check",
    )
    op.drop_constraint(
        "ck_import_batches_row_count",
        "import_batches",
        type_="check",
    )
    op.drop_constraint("ck_import_batches_status", "import_batches", type_="check")
    op.drop_constraint(
        "uq_import_batches_run_kind_sha256",
        "import_batches",
        type_="unique",
    )
    op.drop_constraint(
        "fk_import_batches_supersedes_batch_id_import_batches",
        "import_batches",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_import_batches_run_id_import_runs",
        "import_batches",
        type_="foreignkey",
    )
    op.drop_column("import_batches", "rolled_back_by")
    op.drop_column("import_batches", "confirmed_by")
    op.drop_column("import_batches", "supersedes_batch_id")
    op.drop_column("import_batches", "result_summary")
    op.drop_column("import_batches", "correction_count")
    op.drop_column("import_batches", "mapping_snapshot")
    op.drop_column("import_batches", "source_rows")
    op.drop_column("import_batches", "source_sha256")
    op.drop_column("import_batches", "template_version")
    op.drop_column("import_batches", "run_id")
    op.alter_column(
        "import_batches",
        "status",
        existing_type=sa.String(length=24),
        type_=sa.String(length=16),
        existing_nullable=False,
        postgresql_using="status::varchar(16)",
    )

    op.drop_index("ix_import_runs_status", table_name="import_runs")
    op.drop_index("ix_import_runs_cohort_year", table_name="import_runs")
    op.drop_table("import_runs")

    op.drop_index("ix_students_class_id", table_name="students")
    op.drop_constraint(
        "fk_students_class_id_academic_classes",
        "students",
        type_="foreignkey",
    )
    op.drop_column("students", "class_id")

    op.drop_index("ix_academic_classes_major_id", table_name="academic_classes")
    op.drop_index("ix_academic_classes_cohort_year", table_name="academic_classes")
    op.drop_index("ix_academic_classes_code", table_name="academic_classes")
    op.drop_table("academic_classes")

    op.drop_column("audit_logs", "correlation_id")
    op.drop_column("audit_logs", "source_channel")
    op.drop_column("audit_logs", "actor_role")
