"""Enterprise demo and production portal ownership facts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class EnterpriseAccessToken(Base, TimestampMixin):
    __tablename__ = "enterprise_access_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    submit_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submit_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    submissions = relationship(
        "EnterpriseJobSubmission",
        back_populates="access_token",
        cascade="all, delete-orphan",
    )


class EnterpriseOrganization(Base, TimestampMixin):
    """PRODUCTION enterprise tenant/profile."""

    __tablename__ = "enterprise_organizations"
    __table_args__ = (
        UniqueConstraint("credit_code", name="uq_enterprise_orgs_credit_code"),
        CheckConstraint(
            "status IN ('pending', 'verified', 'suspended')",
            name="ck_enterprise_orgs_status",
        ),
        CheckConstraint("revision >= 1", name="ck_enterprise_orgs_revision_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credit_code: Mapped[str] = mapped_column(String(64), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scale: Mapped[str | None] = mapped_column(String(32), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    website: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_phone_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )


class EnterpriseAccount(Base, TimestampMixin):
    """PRODUCTION enterprise login account bound to one organization."""

    __tablename__ = "enterprise_accounts"
    __table_args__ = (
        UniqueConstraint("username", name="uq_enterprise_accounts_username"),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_enterprise_accounts_id_organization_id",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'suspended')",
            name="ck_enterprise_accounts_status",
        ),
        CheckConstraint(
            "credential_version >= 1",
            name="ck_enterprise_accounts_credential_version_positive",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_enterprise_accounts_revision_positive",
        ),
        CheckConstraint(
            "status <> 'active' OR "
            "(password_hash IS NOT NULL AND activated_at IS NOT NULL)",
            name="ck_enterprise_accounts_active_credentials",
        ),
        Index("ix_enterprise_accounts_org_status", "organization_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise_organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    credential_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization = relationship("EnterpriseOrganization")


class EnterpriseActivationMaterial(Base, TimestampMixin):
    """One-time activation/recovery material; plaintext is never stored."""

    __tablename__ = "enterprise_activation_materials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "organization_id"],
            ["enterprise_accounts.id", "enterprise_accounts.organization_id"],
            ondelete="RESTRICT",
            name="fk_enterprise_activation_materials_account_org",
        ),
        UniqueConstraint(
            "code_digest",
            name="uq_enterprise_activation_materials_code_digest",
        ),
        CheckConstraint(
            "code_digest ~ '^[0-9a-f]{64}$'",
            name="ck_enterprise_activation_materials_code_digest",
        ),
        CheckConstraint(
            "purpose IN ('activation', 'recovery')",
            name="ck_enterprise_activation_materials_purpose",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_enterprise_activation_materials_expiry",
        ),
        CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_enterprise_activation_materials_terminal_state",
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_user_id IS NULL "
            "AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL "
            "AND revoke_reason IS NOT NULL)",
            name="ck_enterprise_activation_materials_revocation",
        ),
        CheckConstraint(
            "char_length(correlation_id) BETWEEN 8 AND 64",
            name="ck_enterprise_activation_materials_correlation_length",
        ),
        Index(
            "uq_enterprise_activation_materials_open_account",
            "account_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        Index(
            "ix_enterprise_activation_materials_correlation_id",
            "correlation_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise_organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    issued_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    issue_reason: Mapped[str] = mapped_column(Text, nullable=False)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)


class EnterpriseSession(Base, TimestampMixin):
    """Server-side production enterprise session."""

    __tablename__ = "enterprise_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "organization_id"],
            ["enterprise_accounts.id", "enterprise_accounts.organization_id"],
            ondelete="RESTRICT",
            name="fk_enterprise_sessions_account_org",
        ),
        UniqueConstraint("jti_digest", name="uq_enterprise_sessions_jti_digest"),
        CheckConstraint(
            "jti_digest ~ '^[0-9a-f]{64}$'",
            name="ck_enterprise_sessions_jti_digest",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_enterprise_sessions_expiry",
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name="ck_enterprise_sessions_revocation",
        ),
        Index("ix_enterprise_sessions_org_expires", "organization_id", "expires_at"),
        Index("ix_enterprise_sessions_account_revoked", "account_id", "revoked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise_organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    jti_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)


class EnterpriseJobSubmission(Base, TimestampMixin):
    __tablename__ = "enterprise_job_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("enterprise_access_tokens.id", ondelete="CASCADE"),
        nullable=True,
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("enterprise_organizations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="queued",
        server_default="queued",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    access_token = relationship("EnterpriseAccessToken", back_populates="submissions")
    organization = relationship("EnterpriseOrganization")
    job = relationship("Job")

    __table_args__ = (
        CheckConstraint(
            "(access_token_id IS NOT NULL AND organization_id IS NULL) OR "
            "(access_token_id IS NULL AND organization_id IS NOT NULL)",
            name="ck_enterprise_job_submissions_single_owner",
        ),
        Index(
            "ix_enterprise_job_submissions_token_status",
            "access_token_id",
            "status",
        ),
        Index(
            "ix_enterprise_job_submissions_org_status",
            "organization_id",
            "status",
        ),
    )


class EnterpriseApplicationEvent(Base, TimestampMixin):
    """Enterprise-side candidate status transition receipt."""

    __tablename__ = "enterprise_application_events"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_enterprise_application_events_idempotency_key",
        ),
        CheckConstraint(
            "idempotency_key ~* "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_enterprise_application_events_uuid4",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_enterprise_application_events_request_hash",
        ),
        CheckConstraint(
            "from_status IN ('submitted', 'interview', 'offered', 'rejected', "
            "'accepted', 'withdrawn')",
            name="ck_enterprise_application_events_from_status",
        ),
        CheckConstraint(
            "to_status IN ('interview', 'offered', 'rejected', 'accepted')",
            name="ck_enterprise_application_events_to_status",
        ),
        Index(
            "ix_enterprise_application_events_org_application",
            "organization_id",
            "application_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise_organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[str] = mapped_column(String(16), nullable=False)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
