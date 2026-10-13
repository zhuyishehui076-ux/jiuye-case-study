"""API 依赖：当前用户解析 + 接口级 RBAC 守卫（SPEC §6.8 X1 第一层）。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Profile, settings
from app.core.rbac import Role
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import ManagementSession, User
from app.services.auth_security import management_session_jti_digest

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")


class CurrentUser:
    def __init__(
        self,
        user_id: int,
        username: str,
        role: Role,
        college_id: int | None,
        credential_release_id: str | None = None,
        session_jti: str | None = None,
    ):
        self.user_id = user_id
        self.username = username
        self.role = role
        self.college_id = college_id
        self.credential_release_id = credential_release_id
        self.session_jti = session_jti


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或过期的凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise cred_exc from None
    username = payload.get("sub")
    role_raw = payload.get("role")
    credential_version = payload.get("credential_version")
    credential_release_id = payload.get("credential_release_id")
    session_jti = payload.get("jti")
    if (
        not isinstance(username, str)
        or not role_raw
        or not isinstance(credential_version, int)
        or isinstance(credential_version, bool)
        or (
            credential_release_id is not None
            and (
                not isinstance(credential_release_id, str)
                or re.fullmatch(r"demo-[0-9a-f]{32}", credential_release_id) is None
            )
        )
    ):
        raise cred_exc
    try:
        role = Role(role_raw)
    except ValueError:
        raise cred_exc from None
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or user.role.name != role.value
        or user.credential_version != credential_version
        or user.college_id != payload.get("college_id")
    ):
        raise cred_exc
    if session_jti is not None:
        if not isinstance(session_jti, str):
            raise cred_exc
        try:
            parsed_jti = UUID(session_jti)
        except ValueError:
            raise cred_exc from None
        if parsed_jti.version != 4 or str(parsed_jti) != session_jti:
            raise cred_exc
        active_session = db.execute(
            select(ManagementSession.id).where(
                ManagementSession.user_id == user.id,
                ManagementSession.jti_digest
                == management_session_jti_digest(session_jti),
                ManagementSession.revoked_at.is_(None),
                ManagementSession.expires_at > datetime.now(UTC),
            )
        ).scalar_one_or_none()
        if active_session is None:
            raise cred_exc
    elif settings.profile is Profile.PRODUCTION:
        # T20 之前签发的无 jti 管理令牌不能进入生产数据档。
        raise cred_exc
    return CurrentUser(
        user_id=user.id,
        username=username,
        role=role,
        college_id=user.college_id,
        credential_release_id=credential_release_id,
        session_jti=session_jti,
    )


def require_roles(*allowed: Role):
    """接口级角色守卫依赖工厂：只有 allowed 中的角色可访问。"""
    allowed_set = set(allowed)

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"角色 {user.role.value} 无权访问此资源",
            )
        # 辅导员的数据权限以学院绑定为根。`None` 在服务层通常表示
        # “不按学院过滤”（就业中心/领导等全校角色），因此绝不能让一个
        # 配置不完整的辅导员把空绑定透传为全校范围。统一在所有管理 API
        # 的角色依赖处失败关闭，避免每个业务入口各自遗漏。
        if user.role is Role.COUNSELOR and user.college_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前辅导员账号未绑定学院，无法访问学院业务数据。",
            )
        return user

    return _dep


def roles_names(roles: Iterable[Role]) -> list[str]:
    return [r.value for r in roles]
