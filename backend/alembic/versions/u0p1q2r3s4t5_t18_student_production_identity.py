"""T18 production student identity, sessions, applications and fair codes.

Revision ID: u0p1q2r3s4t5
Revises: t9n0p1q2r3s4
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "u0p1q2r3s4t5"
down_revision = "t9n0p1q2r3s4"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
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
        sa.Column("created_by", sa.String(length=64), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "student_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "credential_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            ondelete="RESTRICT",
            name="fk_student_accounts_student_id_students",
        ),
        sa.UniqueConstraint(
            "student_id",
            name="uq_student_accounts_student_id",
        ),
        sa.UniqueConstraint(
            "id",
            "student_id",
            name="uq_student_accounts_id_student_id",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended')",
            name="ck_student_accounts_status",
        ),
        sa.CheckConstraint(
            "credential_version >= 1",
            name="ck_student_accounts_credential_version_positive",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_student_accounts_revision_positive",
        ),
        sa.CheckConstraint(
            "status <> 'active' OR "
            "(password_hash IS NOT NULL AND activated_at IS NOT NULL)",
            name="ck_student_accounts_active_credentials",
        ),
    )
    op.create_index(
        "ix_student_accounts_status",
        "student_accounts",
        ["status"],
    )

    op.create_table(
        "student_activation_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by_user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("issue_reason", sa.Text(), nullable=False),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["account_id", "student_id"],
            ["student_accounts.id", "student_accounts.student_id"],
            ondelete="RESTRICT",
            name="fk_student_activation_materials_account_student",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            ondelete="RESTRICT",
            name="fk_student_activation_materials_student_id_students",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_student_activation_materials_issued_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_student_activation_materials_revoked_by_users",
        ),
        sa.UniqueConstraint(
            "code_digest",
            name="uq_student_activation_materials_code_digest",
        ),
        sa.CheckConstraint(
            "code_digest ~ '^[0-9a-f]{64}$'",
            name="ck_student_activation_materials_code_digest",
        ),
        sa.CheckConstraint(
            "purpose IN ('activation', 'recovery')",
            name="ck_student_activation_materials_purpose",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_student_activation_materials_expiry",
        ),
        sa.CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_student_activation_materials_terminal_state",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_user_id IS NULL "
            "AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL "
            "AND revoke_reason IS NOT NULL)",
            name="ck_student_activation_materials_revocation",
        ),
        sa.CheckConstraint(
            "char_length(correlation_id) BETWEEN 8 AND 64",
            name="ck_student_activation_materials_correlation_length",
        ),
    )
    op.create_index(
        "uq_student_activation_materials_open_student",
        "student_activation_materials",
        ["student_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_index(
        "ix_student_activation_materials_correlation_id",
        "student_activation_materials",
        ["correlation_id"],
    )
    op.create_index(
        "ix_student_activation_materials_student_expires",
        "student_activation_materials",
        ["student_id", "expires_at"],
    )

    op.create_table(
        "student_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("jti_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=128), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["account_id", "student_id"],
            ["student_accounts.id", "student_accounts.student_id"],
            ondelete="RESTRICT",
            name="fk_student_sessions_account_student",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            ondelete="RESTRICT",
            name="fk_student_sessions_student_id_students",
        ),
        sa.UniqueConstraint(
            "jti_digest",
            name="uq_student_sessions_jti_digest",
        ),
        sa.CheckConstraint(
            "jti_digest ~ '^[0-9a-f]{64}$'",
            name="ck_student_sessions_jti_digest",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_student_sessions_expiry",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name="ck_student_sessions_revocation",
        ),
    )
    op.create_index(
        "ix_student_sessions_student_expires",
        "student_sessions",
        ["student_id", "expires_at"],
    )
    op.create_index(
        "ix_student_sessions_account_revoked",
        "student_sessions",
        ["account_id", "revoked_at"],
    )

    op.create_table(
        "job_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'submitted'"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "enterprise_disclosure_authorized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "enterprise_disclosure_revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["students.id"],
            ondelete="RESTRICT",
            name="fk_job_applications_student_id_students",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            ondelete="RESTRICT",
            name="fk_job_applications_job_id_jobs",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            ondelete="RESTRICT",
            name="fk_job_applications_match_id_matches",
        ),
        sa.UniqueConstraint(
            "student_id",
            "job_id",
            name="uq_job_applications_student_job",
        ),
        sa.UniqueConstraint(
            "match_id",
            name="uq_job_applications_match_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_job_applications_idempotency_key",
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'withdrawn', 'interview', 'offered', "
            "'rejected', 'accepted')",
            name="ck_job_applications_status",
        ),
        sa.CheckConstraint(
            "idempotency_key ~* "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_job_applications_idempotency_uuid4",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_job_applications_request_hash",
        ),
        sa.CheckConstraint(
            "status_updated_at >= submitted_at",
            name="ck_job_applications_status_chronology",
        ),
        sa.CheckConstraint(
            "enterprise_disclosure_revoked_at IS NULL OR "
            "(enterprise_disclosure_authorized_at IS NOT NULL AND "
            "enterprise_disclosure_revoked_at >= "
            "enterprise_disclosure_authorized_at)",
            name="ck_job_applications_disclosure_chronology",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_job_applications_revision_positive",
        ),
    )
    op.create_index(
        "ix_job_applications_student_status",
        "job_applications",
        ["student_id", "status"],
    )
    op.create_index(
        "ix_job_applications_job_status",
        "job_applications",
        ["job_id", "status"],
    )

    op.add_column(
        "job_actions",
        sa.Column("application_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_job_actions_application_id_job_applications",
        "job_actions",
        "job_applications",
        ["application_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_job_actions_application_id",
        "job_actions",
        ["application_id"],
    )

    op.add_column(
        "fairs",
        sa.Column("student_checkin_code_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "fairs",
        sa.Column("student_checkin_code_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "fairs",
        sa.Column(
            "student_checkin_code_issued_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_fairs_student_checkin_code_digest",
        "fairs",
        ["student_checkin_code_digest"],
    )
    op.create_check_constraint(
        "ck_fairs_student_checkin_code_pair",
        "fairs",
        "(student_checkin_code_digest IS NULL AND "
        "student_checkin_code_hash IS NULL AND "
        "student_checkin_code_issued_at IS NULL) OR "
        "(student_checkin_code_digest IS NOT NULL AND "
        "student_checkin_code_hash IS NOT NULL AND "
        "student_checkin_code_issued_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_fairs_student_checkin_code_digest",
        "fairs",
        "student_checkin_code_digest IS NULL OR "
        "student_checkin_code_digest ~ '^[0-9a-f]{64}$'",
    )

    op.add_column(
        "student_consents",
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_student_consents_revision_positive",
        "student_consents",
        "revision >= 1",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(
        sa.text(
            "LOCK TABLE student_sessions, student_activation_materials, "
            "job_actions, job_applications, student_accounts, fairs, "
            "student_consents "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    evidence_count = int(
        connection.scalar(
            sa.text(
                "SELECT "
                "(SELECT count(*) FROM student_accounts) + "
                "(SELECT count(*) FROM student_activation_materials) + "
                "(SELECT count(*) FROM student_sessions) + "
                "(SELECT count(*) FROM job_applications) + "
                "(SELECT count(*) FROM job_actions "
                " WHERE application_id IS NOT NULL) + "
                "(SELECT count(*) FROM fairs "
                " WHERE student_checkin_code_digest IS NOT NULL) + "
                "(SELECT count(*) FROM student_consents WHERE revision > 1)"
            )
        )
        or 0
    )
    if evidence_count:
        raise RuntimeError(
            "refusing to discard T18 student identity, consent, session, "
            f"application or fair-code evidence (rows={evidence_count})"
        )

    op.drop_constraint(
        "ck_student_consents_revision_positive",
        "student_consents",
        type_="check",
    )
    op.drop_column("student_consents", "revision")

    op.drop_constraint(
        "ck_fairs_student_checkin_code_digest",
        "fairs",
        type_="check",
    )
    op.drop_constraint(
        "ck_fairs_student_checkin_code_pair",
        "fairs",
        type_="check",
    )
    op.drop_constraint(
        "uq_fairs_student_checkin_code_digest",
        "fairs",
        type_="unique",
    )
    op.drop_column("fairs", "student_checkin_code_issued_at")
    op.drop_column("fairs", "student_checkin_code_hash")
    op.drop_column("fairs", "student_checkin_code_digest")

    op.drop_constraint(
        "uq_job_actions_application_id",
        "job_actions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_job_actions_application_id_job_applications",
        "job_actions",
        type_="foreignkey",
    )
    op.drop_column("job_actions", "application_id")

    op.drop_table("job_applications")
    op.drop_table("student_sessions")
    op.drop_table("student_activation_materials")
    op.drop_table("student_accounts")
