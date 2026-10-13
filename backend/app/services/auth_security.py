"""T20 authentication throttling and revocable management sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.core.config import settings
from app.core.security import (
    create_access_token,
    hash_password,
    keyed_security_digest,
    new_session_jti,
    verify_password,
)
from app.models import AuthThrottle, ManagementSession, User


class AuthRateLimited(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("尝试过于频繁，请稍后再试")
        self.retry_after = max(1, retry_after)


class PasswordChangeRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagementSessionGrant:
    access_token: str
    expires_at: datetime
    session_jti: str
    session_id: int


def validate_password_policy(password: str, *, identity: str | None = None) -> str:
    if len(password) < 12:
        raise ValueError("密码至少需要 12 个字符")
    if len(password.encode("utf-8")) > 72:
        raise ValueError("密码按 UTF-8 计不能超过 72 字节")
    if identity and identity.casefold() in password.casefold():
        raise ValueError("密码不能包含完整账号标识")
    if not (
        re.search(r"[a-z]", password)
        and re.search(r"[A-Z]", password)
        and re.search(r"\d", password)
        and re.search(r"[^A-Za-z0-9]", password)
    ):
        raise ValueError("密码须同时包含大写字母、小写字母、数字和符号")
    return password


def _principal_digest(surface: str, principal: str) -> str:
    normalized = principal.strip().casefold()[:256]
    return keyed_security_digest(f"auth-throttle:{surface}", normalized)


def _lock_digest(db: Session, digest: str) -> None:
    unsigned = int(digest[:16], 16)
    signed = unsigned if unsigned < (1 << 63) else unsigned - (1 << 64)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": signed})


def ensure_auth_attempt_allowed(
    db: Session,
    *,
    surface: str,
    principal: str,
) -> None:
    digest = _principal_digest(surface, principal)
    row = db.execute(
        select(AuthThrottle).where(
            AuthThrottle.surface == surface,
            AuthThrottle.principal_digest == digest,
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is not None and row.locked_until is not None and row.locked_until > now:
        raise AuthRateLimited(int((row.locked_until - now).total_seconds()) + 1)


def record_auth_failure(
    db: Session,
    *,
    surface: str,
    principal: str,
) -> AuthThrottle:
    digest = _principal_digest(surface, principal)
    _lock_digest(db, digest)
    row = db.execute(
        select(AuthThrottle)
        .where(
            AuthThrottle.surface == surface,
            AuthThrottle.principal_digest == digest,
        )
        .with_for_update()
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    window = timedelta(minutes=settings.auth_failure_window_minutes)
    if row is None:
        row = AuthThrottle(
            surface=surface,
            principal_digest=digest,
            failure_count=0,
            window_started_at=now,
            created_by="system:auth-throttle",
        )
        db.add(row)
        db.flush()
    elif now - row.window_started_at >= window:
        row.failure_count = 0
        row.window_started_at = now
        row.locked_until = None
    row.failure_count += 1
    row.last_failed_at = now
    if row.failure_count >= settings.auth_failure_limit:
        was_locked = row.locked_until is not None and row.locked_until > now
        row.locked_until = now + timedelta(minutes=settings.auth_lock_minutes)
        if not was_locked:
            record_db(
                db,
                actor="anonymous",
                source_channel=surface,
                action="auth.throttle.lock",
                object_type="auth_principal_digest",
                object_id=digest[:16],
                change_summary={
                    "failure_count": row.failure_count,
                    "locked_until": row.locked_until.isoformat(),
                    "plaintext_recorded": False,
                },
            )
    db.flush()
    return row


def clear_auth_failures(db: Session, *, surface: str, principal: str) -> None:
    digest = _principal_digest(surface, principal)
    _lock_digest(db, digest)
    db.execute(
        delete(AuthThrottle).where(
            AuthThrottle.surface == surface,
            AuthThrottle.principal_digest == digest,
        )
    )


def management_session_jti_digest(jti: str) -> str:
    return keyed_security_digest("management-session", jti)


def create_management_session(
    db: Session,
    *,
    user: User,
    credential_release_id: str | None = None,
) -> ManagementSessionGrant:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.management_session_minutes)
    session_jti = new_session_jti()
    row = ManagementSession(
        user_id=user.id,
        jti_digest=management_session_jti_digest(session_jti),
        expires_at=expires_at,
        created_by=user.username,
    )
    db.add(row)
    db.flush()
    token = create_access_token(
        subject=user.username,
        role=user.role.name,
        college_id=user.college_id,
        credential_version=user.credential_version,
        credential_release_id=credential_release_id,
        session_jti=session_jti,
        expires_at=expires_at,
    )
    return ManagementSessionGrant(
        access_token=token,
        expires_at=expires_at,
        session_jti=session_jti,
        session_id=row.id,
    )


def revoke_management_session(
    db: Session,
    *,
    user_id: int,
    session_jti: str,
    reason: str,
) -> None:
    row = db.execute(
        select(ManagementSession)
        .where(
            ManagementSession.user_id == user_id,
            ManagementSession.jti_digest == management_session_jti_digest(session_jti),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        return
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revoke_reason = reason


def revoke_all_management_sessions(
    db: Session,
    *,
    user_id: int,
    reason: str,
) -> int:
    rows = db.execute(
        select(ManagementSession)
        .where(
            ManagementSession.user_id == user_id,
            ManagementSession.revoked_at.is_(None),
        )
        .with_for_update()
    ).scalars().all()
    now = datetime.now(UTC)
    for row in rows:
        row.revoked_at = now
        row.revoke_reason = reason
    return len(rows)


def change_management_password(
    db: Session,
    *,
    user_id: int,
    current_password: str,
    new_password: str,
    actor: str,
) -> int:
    user = db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(
        current_password, user.password_hash
    ):
        raise PasswordChangeRejected("当前密码错误")
    try:
        validate_password_policy(new_password, identity=user.username)
    except ValueError as exc:
        raise PasswordChangeRejected(str(exc)) from exc
    if verify_password(new_password, user.password_hash):
        raise PasswordChangeRejected("新密码不能与当前密码相同")
    user.password_hash = hash_password(new_password)
    user.credential_version += 1
    user.password_changed_at = datetime.now(UTC)
    revoked_count = revoke_all_management_sessions(
        db,
        user_id=user.id,
        reason="管理账号密码变更",
    )
    record_db(
        db,
        actor=actor,
        actor_id=user.id,
        actor_role=user.role.name,
        source_channel="management",
        action="management.password.change",
        object_type="user",
        object_id=user.id,
        change_summary={
            "credential_version": user.credential_version,
            "revoked_session_count": revoked_count,
            "plaintext_recorded": False,
        },
    )
    return revoked_count
