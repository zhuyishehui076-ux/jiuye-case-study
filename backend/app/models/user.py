"""鉴权基础表：roles / users（SPEC §4 X1 的登录+RBAC 骨架部分）。

注意：这是登录与 RBAC 所需的最小鉴权表，不是业务表。业务表在 T1 建立。
counselor 的数据范围锁本学院，故 users 预留 college_id（外键指向 T1 的
colleges 表，T0 阶段只作为整型载荷，不建外键约束）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 取值对齐 app.core.rbac.Role 枚举
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(128), nullable=True)

    users: Mapped[list[User]] = relationship(back_populates="role")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    credential_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    role: Mapped[Role] = relationship(back_populates="users")

    # counselor 限本学院；其余角色为空。T1 建 colleges 后再补外键。
    college_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ManagementSession(Base, TimestampMixin):
    """服务端管理会话；数据库只保存 JWT ``jti`` 的不可逆摘要。"""

    __tablename__ = "management_sessions"
    __table_args__ = (
        UniqueConstraint("jti_digest", name="uq_management_sessions_jti_digest"),
        CheckConstraint(
            "jti_digest ~ '^[0-9a-f]{64}$'",
            name="ck_management_sessions_jti_digest",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_management_sessions_expiry",
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name="ck_management_sessions_revocation",
        ),
        Index("ix_management_sessions_user_expires", "user_id", "expires_at"),
        Index("ix_management_sessions_user_revoked", "user_id", "revoked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    jti_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AuthThrottle(Base, TimestampMixin):
    """登录失败窗口；账号标识只以安装密钥派生摘要持久化。"""

    __tablename__ = "auth_throttles"
    __table_args__ = (
        UniqueConstraint(
            "surface", "principal_digest", name="uq_auth_throttles_surface_principal"
        ),
        CheckConstraint(
            "principal_digest ~ '^[0-9a-f]{64}$'",
            name="ck_auth_throttles_principal_digest",
        ),
        CheckConstraint(
            "failure_count >= 0",
            name="ck_auth_throttles_failure_count",
        ),
        CheckConstraint(
            "locked_until IS NULL OR locked_until >= window_started_at",
            name="ck_auth_throttles_lock_window",
        ),
        Index("ix_auth_throttles_locked_until", "locked_until"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    surface: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
