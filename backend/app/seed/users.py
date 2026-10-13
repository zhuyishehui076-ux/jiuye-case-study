"""种子：四角色演示账号 + 角色表（登录 DONE 判据所需）。

四角色各一个账号（SPEC §3 演示账号；§4 四角色）。
counselor 绑定一个占位学院 id=1（T1 建 colleges 后对齐）。
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import require_demo_profile, settings
from app.core.rbac import Role as RoleEnum
from app.core.security import hash_password
from app.models.user import Role, User
from app.services.installation import ensure_installation_profile

# 演示账号元数据：用户名 -> (显示名, 角色, 学院id)。明文口令不属于源码。
DEMO_ACCOUNTS: dict[str, tuple[str, RoleEnum, int | None]] = {
    "admin": ("系统管理员", RoleEnum.ADMIN, None),
    "office": ("就业办老师", RoleEnum.EMPLOYMENT_OFFICE, None),
    "counselor": ("辅导员(计算机学院)", RoleEnum.COUNSELOR, 1),
    "leader": ("校领导(只读)", RoleEnum.LEADER_READONLY, None),
}

ROLE_DESCRIPTIONS = {
    RoleEnum.ADMIN: "系统管理员，全量权限",
    RoleEnum.EMPLOYMENT_OFFICE: "就业办，全校范围",
    RoleEnum.COUNSELOR: "辅导员，限本学院",
    RoleEnum.LEADER_READONLY: "校领导，只读",
}


def ensure_roles(db: Session) -> dict[RoleEnum, Role]:
    result: dict[RoleEnum, Role] = {}
    for role_enum in RoleEnum:
        row = db.execute(
            select(Role).where(Role.name == role_enum.value)
        ).scalar_one_or_none()
        if row is None:
            row = Role(
                name=role_enum.value,
                description=ROLE_DESCRIPTIONS.get(role_enum, ""),
                created_by="seed",
            )
            db.add(row)
            db.flush()
        result[role_enum] = row
    return result


def seed_demo_users(
    db: Session,
    *,
    commit: bool = True,
    passwords: Mapping[str, str] | None = None,
    counselor_college_id: int | None = None,
) -> int:
    """Ensure Demo account metadata without embedding a usable default password.

    A caller may inject an explicit password mapping through process memory (tests
    and the credential release promoter).  Otherwise a random value is hashed
    and discarded so a newly seeded account cannot be logged into until a
    credential release is activated.
    """
    require_demo_profile("seed_demo_users")
    ensure_installation_profile(
        db,
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )
    roles = ensure_roles(db)
    created = 0
    if passwords is not None and set(passwords) != set(DEMO_ACCOUNTS):
        raise ValueError("password mapping must contain exactly the Demo accounts")
    for username, (display, role_enum, default_college_id) in DEMO_ACCOUNTS.items():
        college_id = (
            counselor_college_id
            if role_enum is RoleEnum.COUNSELOR and counselor_college_id is not None
            else default_college_id
        )
        exists = db.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if exists is not None:
            # 内置场景的组织主键会因预检事务推进序列，不能假定学院始终为 id=1。
            # 每次启用都把固定演示账号重新对齐到本次生成的角色与学院。
            exists.display_name = display
            exists.role_id = roles[role_enum].id
            exists.college_id = college_id
            exists.is_active = True
            if passwords is not None:
                exists.password_hash = hash_password(passwords[username])
            continue
        password = passwords[username] if passwords is not None else secrets.token_urlsafe(32)
        db.add(
            User(
                username=username,
                display_name=display,
                password_hash=hash_password(password),
                role_id=roles[role_enum].id,
                college_id=college_id,
                is_active=True,
                created_by="seed",
            )
        )
        del password
        created += 1
    if commit:
        db.commit()
    else:
        db.flush()
    return created
