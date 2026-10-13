"""密码哈希 + JWT 令牌签发/校验（自建账号密码 + JWT，SPEC §2）。"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import bcrypt
import jwt

from app.core.config import settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    subject: str,
    role: str,
    college_id: int | None = None,
    *,
    credential_version: int = 1,
    credential_release_id: str | None = None,
    session_jti: str | None = None,
    expires_at: datetime | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "college_id": college_id,
        "credential_version": credential_version,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": expires_at or now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    if credential_release_id is not None:
        payload["credential_release_id"] = credential_release_id
    if session_jti is not None:
        payload["jti"] = session_jti
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_student_access_token(
    *,
    student_id: int,
    student_no: str,
    credential_version: int = 1,
    credential_release_id: str | None = None,
    session_jti: str | None = None,
    expires_at: datetime | None = None,
) -> str:
    """签发只携带本人学生主键的受限 scope。

    Demo tokens intentionally omit ``jti`` for compatibility. Production
    sessions pass an opaque UUID and persist only its keyed digest.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": f"student:{student_id}",
        "role": "student",
        "scope": "student",
        "student_id": student_id,
        "student_no": student_no,
        "credential_version": credential_version,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": expires_at or now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    if credential_release_id is not None:
        payload["credential_release_id"] = credential_release_id
    if session_jti is not None:
        payload["jti"] = session_jti
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_enterprise_access_token(
    *,
    organization_id: int,
    account_id: int,
    username: str,
    credential_version: int,
    session_jti: str,
    expires_at: datetime | None = None,
) -> str:
    """Issue a restricted enterprise JWT for production enterprise H5."""
    now = datetime.now(UTC)
    payload = {
        "sub": f"enterprise:{account_id}",
        "role": "enterprise",
        "scope": "enterprise",
        "organization_id": organization_id,
        "account_id": account_id,
        "username": username,
        "credential_version": credential_version,
        "jti": session_jti,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": expires_at or now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """校验并解码；失败抛 jwt.PyJWTError 子类，由调用方转 401。"""
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        leeway=5,
        options={"require": ["sub", "role", "iss", "aud", "iat", "nbf", "exp"]},
    )


def new_session_jti() -> str:
    return str(uuid4())


def keyed_security_digest(namespace: str, value: str) -> str:
    """使用安装密钥生成不可逆索引，避免持久化账号、令牌或 IP 明文。"""
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
        f"{namespace}\0{value}".encode(),
        hashlib.sha256,
    ).hexdigest()
