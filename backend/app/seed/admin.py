"""P-LOCAL install-time administrator initialization (SPEC T13)."""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import Role as RoleEnum
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User
from app.seed.users import ensure_roles
from app.services.auth_security import (
    revoke_all_management_sessions,
    validate_password_policy,
)

MIN_PASSWORD_LENGTH = 12


def upsert_admin(
    db: Session,
    *,
    username: str,
    display_name: str,
    password: str,
) -> User:
    username = username.strip()
    display_name = display_name.strip()
    if not username:
        raise ValueError("administrator username cannot be empty")
    if not display_name:
        raise ValueError("administrator display name cannot be empty")
    validate_password_policy(password, identity=username)

    roles = ensure_roles(db)
    admin_role = roles[RoleEnum.ADMIN]
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None:
        user = User(
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            is_active=True,
            role_id=admin_role.id,
            college_id=None,
            created_by="install",
        )
        db.add(user)
    else:
        user.display_name = display_name
        user.password_hash = hash_password(password)
        user.password_changed_at = datetime.now(UTC)
        user.credential_version += 1
        user.is_active = True
        user.role_id = admin_role.id
        user.college_id = None
        revoke_all_management_sessions(
            db,
            user_id=user.id,
            reason="管理员安装初始化或口令轮换",
        )
    db.commit()
    db.refresh(user)
    return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.seed.admin")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--display-name", default="系统管理员")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="从 stdin 读取一行口令，避免口令出现在进程参数中",
    )
    args = parser.parse_args(argv)

    password = (
        sys.stdin.readline().rstrip("\r\n")
        if args.password_stdin
        else getpass.getpass("管理员口令：")
    )
    with SessionLocal() as db:
        user = upsert_admin(
            db,
            username=args.username,
            display_name=args.display_name,
            password=password,
        )
    print(f"[admin-init] administrator ready: {user.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
