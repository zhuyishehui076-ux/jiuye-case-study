"""T19 production enterprise identity and ownership.

Revision ID: v1q2r3s4t5u6
Revises: u0p1q2r3s4t5
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "v1q2r3s4t5u6"
down_revision = "u0p1q2r3s4t5"
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
        "enterprise_organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legal_name", sa.String(length=128), nullable=False),
        sa.Column("short_name", sa.String(length=64), nullable=True),
        sa.Column("credit_code", sa.String(length=64), nullable=False),
        sa.Column("industry", sa.String(length=64), nullable=True),
        sa.Column("scale", sa.String(length=32), nullable=True),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column("address", sa.String(length=128), nullable=True),
        sa.Column("website", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("contact_name", sa.String(length=64), nullable=True),
        sa.Column("contact_phone_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        *_timestamps(),
        sa.UniqueConstraint("credit_code", name="uq_enterprise_orgs_credit_code"),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'suspended')",
            name="ck_enterprise_orgs_status",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_enterprise_orgs_revision_positive",
        ),
    )
    op.create_index(
        "ix_enterprise_organizations_status",
        "enterprise_organizations",
        ["status"],
    )

    op.create_table(
        "enterprise_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
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
            ["organization_id"],
            ["enterprise_organizations.id"],
            ondelete="RESTRICT",
            name="fk_enterprise_accounts_org",
        ),
        sa.UniqueConstraint("username", name="uq_enterprise_accounts_username"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_enterprise_accounts_id_organization_id",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended')",
            name="ck_enterprise_accounts_status",
        ),
        sa.CheckConstraint(
            "credential_version >= 1",
            name="ck_enterprise_accounts_credential_version_positive",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_enterprise_accounts_revision_positive",
        ),
        sa.CheckConstraint(
            "status <> 'active' OR "
            "(password_hash IS NOT NULL AND activated_at IS NOT NULL)",
            name="ck_enterprise_accounts_active_credentials",
        ),
    )
    op.create_index(
        "ix_enterprise_accounts_status",
        "enterprise_accounts",
        ["status"],
    )
    op.create_index(
        "ix_enterprise_accounts_org_status",
        "enterprise_accounts",
        ["organization_id", "status"],
    )

    op.create_table(
        "enterprise_activation_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
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
            ["account_id", "organization_id"],
            ["enterprise_accounts.id", "enterprise_accounts.organization_id"],
            ondelete="RESTRICT",
            name="fk_enterprise_activation_materials_account_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.id"],
            ondelete="RESTRICT",
            name="fk_enterprise_activation_materials_org",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_enterprise_activation_materials_issued_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_enterprise_activation_materials_revoked_by_users",
        ),
        sa.UniqueConstraint(
            "code_digest",
            name="uq_enterprise_activation_materials_code_digest",
        ),
        sa.CheckConstraint(
            "code_digest ~ '^[0-9a-f]{64}$'",
            name="ck_enterprise_activation_materials_code_digest",
        ),
        sa.CheckConstraint(
            "purpose IN ('activation', 'recovery')",
            name="ck_enterprise_activation_materials_purpose",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_enterprise_activation_materials_expiry",
        ),
        sa.CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_enterprise_activation_materials_terminal_state",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_user_id IS NULL "
            "AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL "
            "AND revoke_reason IS NOT NULL)",
            name="ck_enterprise_activation_materials_revocation",
        ),
        sa.CheckConstraint(
            "char_length(correlation_id) BETWEEN 8 AND 64",
            name="ck_enterprise_activation_materials_correlation_length",
        ),
    )
    op.create_index(
        "uq_enterprise_activation_materials_open_account",
        "enterprise_activation_materials",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_index(
        "ix_enterprise_activation_materials_correlation_id",
        "enterprise_activation_materials",
        ["correlation_id"],
    )

    op.create_table(
        "enterprise_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("jti_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=128), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["account_id", "organization_id"],
            ["enterprise_accounts.id", "enterprise_accounts.organization_id"],
            ondelete="RESTRICT",
            name="fk_enterprise_sessions_account_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.id"],
            ondelete="RESTRICT",
            name="fk_enterprise_sessions_org",
        ),
        sa.UniqueConstraint("jti_digest", name="uq_enterprise_sessions_jti_digest"),
        sa.CheckConstraint(
            "jti_digest ~ '^[0-9a-f]{64}$'",
            name="ck_enterprise_sessions_jti_digest",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_enterprise_sessions_expiry",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name="ck_enterprise_sessions_revocation",
        ),
    )
    op.create_index(
        "ix_enterprise_sessions_org_expires",
        "enterprise_sessions",
        ["organization_id", "expires_at"],
    )
    op.create_index(
        "ix_enterprise_sessions_account_revoked",
        "enterprise_sessions",
        ["account_id", "revoked_at"],
    )

    op.add_column(
        "jobs",
        sa.Column("enterprise_organization_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_enterprise_organization_id_enterprise_orgs",
        "jobs",
        "enterprise_organizations",
        ["enterprise_organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_jobs_enterprise_organization_id",
        "jobs",
        ["enterprise_organization_id"],
    )

    op.add_column(
        "enterprise_job_submissions",
        sa.Column("organization_id", sa.Integer(), nullable=True),
    )
    op.alter_column(
        "enterprise_job_submissions",
        "access_token_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_enterprise_job_submissions_org",
        "enterprise_job_submissions",
        "enterprise_organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_enterprise_job_submissions_single_owner",
        "enterprise_job_submissions",
        "(access_token_id IS NOT NULL AND organization_id IS NULL) OR "
        "(access_token_id IS NULL AND organization_id IS NOT NULL)",
    )
    op.create_index(
        "ix_enterprise_job_submissions_org_status",
        "enterprise_job_submissions",
        ["organization_id", "status"],
    )

    op.add_column(
        "fair_companies",
        sa.Column("enterprise_organization_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_fair_companies_enterprise_organization_id_enterprise_orgs",
        "fair_companies",
        "enterprise_organizations",
        ["enterprise_organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_fair_companies_enterprise_organization_id",
        "fair_companies",
        ["enterprise_organization_id"],
    )

    op.create_table(
        "enterprise_application_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=False),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["enterprise_organizations.id"],
            ondelete="RESTRICT",
            name="fk_enterprise_application_events_org",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["enterprise_accounts.id"],
            ondelete="RESTRICT",
            name="fk_enterprise_application_events_account",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["job_applications.id"],
            ondelete="RESTRICT",
            name="fk_enterprise_application_events_application",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_enterprise_application_events_idempotency_key",
        ),
        sa.CheckConstraint(
            "idempotency_key ~* "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_enterprise_application_events_uuid4",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_enterprise_application_events_request_hash",
        ),
        sa.CheckConstraint(
            "from_status IN ('submitted', 'interview', 'offered', 'rejected', "
            "'accepted', 'withdrawn')",
            name="ck_enterprise_application_events_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('interview', 'offered', 'rejected', 'accepted')",
            name="ck_enterprise_application_events_to_status",
        ),
    )
    op.create_index(
        "ix_enterprise_application_events_org_application",
        "enterprise_application_events",
        ["organization_id", "application_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(
        sa.text(
            "LOCK TABLE enterprise_application_events, enterprise_sessions, "
            "enterprise_activation_materials, enterprise_accounts, "
            "enterprise_organizations, enterprise_job_submissions, jobs, "
            "fair_companies IN ACCESS EXCLUSIVE MODE"
        )
    )
    evidence_count = int(
        connection.scalar(
            sa.text(
                "SELECT "
                "(SELECT count(*) FROM enterprise_organizations) + "
                "(SELECT count(*) FROM enterprise_accounts) + "
                "(SELECT count(*) FROM enterprise_activation_materials) + "
                "(SELECT count(*) FROM enterprise_sessions) + "
                "(SELECT count(*) FROM enterprise_application_events) + "
                "(SELECT count(*) FROM enterprise_job_submissions "
                " WHERE organization_id IS NOT NULL) + "
                "(SELECT count(*) FROM jobs "
                " WHERE enterprise_organization_id IS NOT NULL) + "
                "(SELECT count(*) FROM fair_companies "
                " WHERE enterprise_organization_id IS NOT NULL)"
            )
        )
        or 0
    )
    if evidence_count:
        raise RuntimeError(
            "refusing to discard T19 enterprise identity, ownership, session "
            f"or candidate evidence (rows={evidence_count})"
        )

    op.drop_index(
        "ix_enterprise_application_events_org_application",
        table_name="enterprise_application_events",
    )
    op.drop_table("enterprise_application_events")

    op.drop_index(
        "ix_fair_companies_enterprise_organization_id",
        table_name="fair_companies",
    )
    op.drop_constraint(
        "fk_fair_companies_enterprise_organization_id_enterprise_orgs",
        "fair_companies",
        type_="foreignkey",
    )
    op.drop_column("fair_companies", "enterprise_organization_id")

    op.drop_index(
        "ix_enterprise_job_submissions_org_status",
        table_name="enterprise_job_submissions",
    )
    op.drop_constraint(
        "ck_enterprise_job_submissions_single_owner",
        "enterprise_job_submissions",
        type_="check",
    )
    op.drop_constraint(
        "fk_enterprise_job_submissions_org",
        "enterprise_job_submissions",
        type_="foreignkey",
    )
    op.alter_column(
        "enterprise_job_submissions",
        "access_token_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("enterprise_job_submissions", "organization_id")

    op.drop_index("ix_jobs_enterprise_organization_id", table_name="jobs")
    op.drop_constraint(
        "fk_jobs_enterprise_organization_id_enterprise_orgs",
        "jobs",
        type_="foreignkey",
    )
    op.drop_column("jobs", "enterprise_organization_id")

    op.drop_table("enterprise_sessions")
    op.drop_table("enterprise_activation_materials")
    op.drop_index("ix_enterprise_accounts_org_status", table_name="enterprise_accounts")
    op.drop_index("ix_enterprise_accounts_status", table_name="enterprise_accounts")
    op.drop_table("enterprise_accounts")
    op.drop_index(
        "ix_enterprise_organizations_status",
        table_name="enterprise_organizations",
    )
    op.drop_table("enterprise_organizations")
