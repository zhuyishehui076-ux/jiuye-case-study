"""学生端演示凭据、正式身份、会话与数据用途授权。"""

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


class StudentBinding(Base, TimestampMixin):
    __tablename__ = "student_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    binding_code_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    binding_code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    credential_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    student = relationship("Student")


class StudentAccount(Base, TimestampMixin):
    """PRODUCTION 学生正式账户；DEMO 继续使用 ``StudentBinding``。"""

    __tablename__ = "student_accounts"
    __table_args__ = (
        UniqueConstraint("student_id", name="uq_student_accounts_student_id"),
        # 子表用 (account_id, student_id) 复合外键锁定账户归属。
        UniqueConstraint(
            "id",
            "student_id",
            name="uq_student_accounts_id_student_id",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'suspended')",
            name="ck_student_accounts_status",
        ),
        CheckConstraint(
            "credential_version >= 1",
            name="ck_student_accounts_credential_version_positive",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_student_accounts_revision_positive",
        ),
        CheckConstraint(
            "status <> 'active' OR "
            "(password_hash IS NOT NULL AND activated_at IS NOT NULL)",
            name="ck_student_accounts_active_credentials",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
    )
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

    student = relationship("Student")


class StudentActivationMaterial(Base, TimestampMixin):
    """限时一次性激活/恢复材料；原文永不落库。"""

    __tablename__ = "student_activation_materials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "student_id"],
            ["student_accounts.id", "student_accounts.student_id"],
            ondelete="RESTRICT",
            name="fk_student_activation_materials_account_student",
        ),
        UniqueConstraint(
            "code_digest",
            name="uq_student_activation_materials_code_digest",
        ),
        CheckConstraint(
            "code_digest ~ '^[0-9a-f]{64}$'",
            name="ck_student_activation_materials_code_digest",
        ),
        CheckConstraint(
            "purpose IN ('activation', 'recovery')",
            name="ck_student_activation_materials_purpose",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_student_activation_materials_expiry",
        ),
        CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_student_activation_materials_terminal_state",
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_user_id IS NULL "
            "AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL "
            "AND revoke_reason IS NOT NULL)",
            name="ck_student_activation_materials_revocation",
        ),
        CheckConstraint(
            "char_length(correlation_id) BETWEEN 8 AND 64",
            name="ck_student_activation_materials_correlation_length",
        ),
        Index(
            "uq_student_activation_materials_open_student",
            "student_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        Index(
            "ix_student_activation_materials_correlation_id",
            "correlation_id",
        ),
        Index(
            "ix_student_activation_materials_student_expires",
            "student_id",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
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


class StudentSession(Base, TimestampMixin):
    """服务端学生会话；只保存 JWT ``jti`` 的不可逆摘要。"""

    __tablename__ = "student_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "student_id"],
            ["student_accounts.id", "student_accounts.student_id"],
            ondelete="RESTRICT",
            name="fk_student_sessions_account_student",
        ),
        UniqueConstraint("jti_digest", name="uq_student_sessions_jti_digest"),
        CheckConstraint(
            "jti_digest ~ '^[0-9a-f]{64}$'",
            name="ck_student_sessions_jti_digest",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_student_sessions_expiry",
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name="ck_student_sessions_revocation",
        ),
        Index(
            "ix_student_sessions_student_expires",
            "student_id",
            "expires_at",
        ),
        Index(
            "ix_student_sessions_account_revoked",
            "account_id",
            "revoked_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    jti_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)


class JobApplication(Base, TimestampMixin):
    """学生岗位投递唯一事实源。"""

    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "job_id",
            name="uq_job_applications_student_job",
        ),
        UniqueConstraint("match_id", name="uq_job_applications_match_id"),
        UniqueConstraint(
            "idempotency_key",
            name="uq_job_applications_idempotency_key",
        ),
        CheckConstraint(
            "status IN ('submitted', 'withdrawn', 'interview', 'offered', "
            "'rejected', 'accepted')",
            name="ck_job_applications_status",
        ),
        CheckConstraint(
            "idempotency_key ~* "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_job_applications_idempotency_uuid4",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_job_applications_request_hash",
        ),
        CheckConstraint(
            "status_updated_at >= submitted_at",
            name="ck_job_applications_status_chronology",
        ),
        CheckConstraint(
            "enterprise_disclosure_revoked_at IS NULL OR "
            "(enterprise_disclosure_authorized_at IS NOT NULL AND "
            "enterprise_disclosure_revoked_at >= "
            "enterprise_disclosure_authorized_at)",
            name="ck_job_applications_disclosure_chronology",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_job_applications_revision_positive",
        ),
        Index(
            "ix_job_applications_student_status",
            "student_id",
            "status",
        ),
        Index(
            "ix_job_applications_job_status",
            "job_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    match_id: Mapped[int | None] = mapped_column(
        ForeignKey("matches.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="submitted",
        server_default=text("'submitted'"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    enterprise_disclosure_authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enterprise_disclosure_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )


class StudentConsent(Base, TimestampMixin):
    __tablename__ = "student_consents"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_student_consents_revision_positive",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    recommendation_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    growth_profile_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    job_action_tracking_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    student = relationship("Student")
