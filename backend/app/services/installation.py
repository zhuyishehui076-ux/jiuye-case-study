"""数据库持久化不可变安装档位（SPEC v1.7 §3/§14.3）。"""

from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import Table, literal, select, text
from sqlalchemy.orm import Session

from app.core.config import Profile, get_settings
from app.db.base import Base
from app.models import AppSetting, User

INSTALLATION_PROFILE_KEY = "system_installation_profile"
DEMO_DATASET_MARKER_KEY = "demo_dataset_marker"
DEMO_DATASET_ID = "school-employment-closed-loop-v1"

_INSTALLATION_LOCK_KEY = 746_201_507_133
_NON_BUSINESS_TABLES = {"app_settings", "roles", "users"}
_DEMO_CREATORS = (
    "seed",
    "s6-story-seed",
    "s6-boundary-seed",
    "d2-seed",
    "demo-scenario",
)


class InstallationProfileError(RuntimeError):
    """数据库内容或不可变安装档位与启动配置冲突。"""


PROFILE_MISMATCH_MARKER = "T15_PROFILE_MISMATCH"


def _acquire_installation_lock(db: Session, *, timeout_seconds: float) -> None:
    if timeout_seconds <= 0:
        raise ValueError("lock_timeout_seconds must be positive")
    deadline = time.monotonic() + timeout_seconds
    while True:
        acquired = bool(
            db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                {"lock_key": _INSTALLATION_LOCK_KEY},
            )
        )
        if acquired:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise InstallationProfileError(
                "数据库安装身份锁等待超时；拒绝继续，请稍后重试。"
            )
        time.sleep(min(0.05, remaining))


def _business_tables() -> tuple[Table, ...]:
    return tuple(
        table
        for table in Base.metadata.sorted_tables
        if table.name not in _NON_BUSINESS_TABLES
    )


def _table_has_rows(db: Session, table: Table) -> bool:
    return bool(db.scalar(select(literal(True)).select_from(table).limit(1)))


def _has_any_database_content(db: Session) -> bool:
    if bool(db.scalar(select(literal(True)).select_from(User).limit(1))):
        return True
    if any(_table_has_rows(db, table) for table in _business_tables()):
        return True
    return bool(
        db.scalar(
            select(literal(True))
            .select_from(AppSetting)
            .where(AppSetting.key != INSTALLATION_PROFILE_KEY)
            .limit(1)
        )
    )


def _demo_dataset_marker(db: Session) -> AppSetting | None:
    return db.scalar(
        select(AppSetting).where(AppSetting.key == DEMO_DATASET_MARKER_KEY)
    )


def _is_trusted_demo_dataset_marker(marker: AppSetting) -> bool:
    value = marker.value
    return (
        isinstance(value, dict)
        and value.get("dataset_id") == DEMO_DATASET_ID
        and value.get("mock_only") is True
    )


def _has_demo_accounts(db: Session) -> bool:
    return bool(
        db.scalar(
            select(literal(True))
            .select_from(User)
            .where(User.created_by.in_(_DEMO_CREATORS))
            .limit(1)
        )
    )


def _has_demo_business_data(db: Session) -> bool:
    for table in _business_tables():
        created_by = table.c.get("created_by")
        if created_by is None:
            continue
        if db.scalar(
            select(literal(True))
            .select_from(table)
            .where(created_by.in_(_DEMO_CREATORS))
            .limit(1)
        ):
            return True
    return False


def _stored_identity(marker: AppSetting) -> tuple[Profile, str]:
    value = marker.value
    if (
        not isinstance(value, dict)
        or set(value) != {"profile", "installation_id"}
        or not isinstance(value.get("installation_id"), str)
        or not value["installation_id"]
        or marker.created_by != "system"
    ):
        raise InstallationProfileError(
            "数据库安装身份标记无效；拒绝提供业务服务，请先人工核验数据库来源。"
        )
    try:
        profile = Profile(value["profile"])
    except (TypeError, ValueError):
        raise InstallationProfileError(
            "数据库安装身份标记无效；拒绝提供业务服务，请先人工核验数据库来源。"
        ) from None
    return profile, value["installation_id"]


def ensure_installation_profile(
    db: Session,
    *,
    expected_profile: Profile,
    expected_installation_id: str | None = None,
    lock_timeout_seconds: float = 5,
) -> Profile:
    """校验或首次写入数据库安装档位；事务提交由调用方负责。

    仅可信的 S6 ``demo_dataset_marker`` 允许旧 Demo 库自动补标。其余未标记
    非空数据库拒绝自动归类，避免把备份或业务库误认成另一档位。
    """
    if not isinstance(expected_profile, Profile):
        raise TypeError("expected_profile must be a Profile")
    if expected_installation_id is None:
        expected_installation_id = get_settings().installation_id
    if not isinstance(expected_installation_id, str) or not expected_installation_id:
        raise TypeError("expected_installation_id must be a non-empty string")
    if db.get_bind().dialect.name != "postgresql":
        raise InstallationProfileError("安装档位持久化仅支持 PostgreSQL。")

    with db.no_autoflush:
        _acquire_installation_lock(db, timeout_seconds=lock_timeout_seconds)
        marker = db.scalar(
            select(AppSetting).where(AppSetting.key == INSTALLATION_PROFILE_KEY)
        )
        demo_dataset_marker = _demo_dataset_marker(db)
        trusted_demo_dataset_marker = bool(
            demo_dataset_marker
            and _is_trusted_demo_dataset_marker(demo_dataset_marker)
        )

        if expected_profile is Profile.PRODUCTION:
            if demo_dataset_marker is not None:
                raise InstallationProfileError(
                    f"{PROFILE_MISMATCH_MARKER}: 生产档位拒绝启动："
                    "数据库含 S6 Demo 数据标记，安装档位不可写入或覆盖。"
                )
            if _has_demo_accounts(db):
                raise InstallationProfileError(
                    f"{PROFILE_MISMATCH_MARKER}: 生产档位拒绝启动："
                    "数据库含 Demo 账号，安装档位不可写入或覆盖。"
                )
            if _has_demo_business_data(db):
                raise InstallationProfileError(
                    f"{PROFILE_MISMATCH_MARKER}: 生产档位拒绝启动："
                    "数据库含 Demo 业务数据，安装档位不可写入或覆盖。"
                )
        elif demo_dataset_marker is not None and not trusted_demo_dataset_marker:
            raise InstallationProfileError(
                "Demo 数据标记无效：仅允许精确匹配内置 S6 Mock 数据集的旧库自动补标。"
            )

        if marker is not None:
            stored_profile, stored_installation_id = _stored_identity(marker)
            if stored_profile is not expected_profile:
                raise InstallationProfileError(
                    f"{PROFILE_MISMATCH_MARKER}: 数据库安装档位不一致："
                    f"已持久化 {stored_profile.value}，启动配置为 {expected_profile.value}；"
                    "拒绝提供业务服务。"
                )
            if stored_installation_id != expected_installation_id:
                raise InstallationProfileError(
                    "数据库安装身份不一致：当前配置试图复用另一安装实例的数据库；"
                    "拒绝提供业务服务。"
                )
            return stored_profile

        if not (expected_profile is Profile.DEMO and trusted_demo_dataset_marker):
            if _has_any_database_content(db):
                raise InstallationProfileError(
                    "数据库尚无安装档位标记但已存在账号、设置或业务数据；"
                    "拒绝自动归类，请先人工核验数据库来源。"
                )

        db.add(
            AppSetting(
                key=INSTALLATION_PROFILE_KEY,
                value={
                    "profile": expected_profile.value,
                    "installation_id": expected_installation_id,
                },
                description=(
                    "系统不可变安装数据档位与安装身份；禁止通过环境变量切换复用数据库"
                ),
                created_by="system",
            )
        )
        db.flush()
        return expected_profile


def verify_installation_profile(
    db: Session,
    *,
    expected_profile: Profile,
    expected_installation_id: str | None = None,
) -> Profile:
    """只读核验现有数据库安装身份；缺失标记时绝不自动认领。"""
    if not isinstance(expected_profile, Profile):
        raise TypeError("expected_profile must be a Profile")
    if expected_installation_id is None:
        expected_installation_id = get_settings().installation_id
    if not isinstance(expected_installation_id, str) or not expected_installation_id:
        raise TypeError("expected_installation_id must be a non-empty string")
    if db.get_bind().dialect.name != "postgresql":
        raise InstallationProfileError("安装档位持久化仅支持 PostgreSQL。")

    with db.no_autoflush:
        marker = db.scalar(
            select(AppSetting).where(AppSetting.key == INSTALLATION_PROFILE_KEY)
        )
        if marker is None:
            raise InstallationProfileError(
                "数据库缺少不可变安装身份标记；只读核验拒绝自动认领。"
            )

        demo_dataset_marker = _demo_dataset_marker(db)
        if expected_profile is Profile.PRODUCTION:
            if demo_dataset_marker is not None:
                raise InstallationProfileError(
                    f"{PROFILE_MISMATCH_MARKER}: 生产档位拒绝启动："
                    "数据库含 S6 Demo 数据标记。"
                )
            if _has_demo_accounts(db):
                raise InstallationProfileError(
                    f"{PROFILE_MISMATCH_MARKER}: 生产档位拒绝启动："
                    "数据库含 Demo 账号。"
                )
            if _has_demo_business_data(db):
                raise InstallationProfileError(
                    f"{PROFILE_MISMATCH_MARKER}: 生产档位拒绝启动："
                    "数据库含 Demo 业务数据。"
                )
        elif demo_dataset_marker is not None and not _is_trusted_demo_dataset_marker(
            demo_dataset_marker
        ):
            raise InstallationProfileError("Demo 数据标记无效，拒绝提供业务服务。")

        stored_profile, stored_installation_id = _stored_identity(marker)
        if stored_profile is not expected_profile:
            raise InstallationProfileError(
                f"{PROFILE_MISMATCH_MARKER}: 数据库安装档位不一致："
                f"已持久化 {stored_profile.value}，启动配置为 {expected_profile.value}；"
                "拒绝提供业务服务。"
            )
        if stored_installation_id != expected_installation_id:
            raise InstallationProfileError(
                "数据库安装身份不一致：当前配置试图复用另一安装实例的数据库；"
                "拒绝提供业务服务。"
            )
        return stored_profile


def preflight_existing_database(
    db: Session,
    *,
    expected_profile: Profile,
    expected_installation_id: str | None = None,
) -> str:
    """迁移前只读核验已有数据库；仅全新空库或可信 S6 Demo 旧库可继续。"""
    if not isinstance(expected_profile, Profile):
        raise TypeError("expected_profile must be a Profile")
    if expected_installation_id is None:
        expected_installation_id = get_settings().installation_id
    if not isinstance(expected_installation_id, str) or not expected_installation_id:
        raise TypeError("expected_installation_id must be a non-empty string")
    if db.get_bind().dialect.name != "postgresql":
        raise InstallationProfileError("安装档位持久化仅支持 PostgreSQL。")

    db.execute(text("SET TRANSACTION READ ONLY"))
    app_settings_exists = bool(
        db.scalar(text("SELECT to_regclass('public.app_settings') IS NOT NULL"))
    )
    if not app_settings_exists:
        public_table_exists = bool(
            db.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_tables "
                    "WHERE schemaname = 'public' LIMIT 1)"
                )
            )
        )
        if public_table_exists:
            raise InstallationProfileError(
                "数据库已有结构但缺少安装身份表；迁移前拒绝自动归类。"
            )
        return "fresh"

    identity = db.execute(
        text(
            "SELECT value, created_by FROM app_settings "
            "WHERE key = :key LIMIT 1"
        ),
        {"key": INSTALLATION_PROFILE_KEY},
    ).mappings().one_or_none()
    if identity is not None:
        value = identity["value"]
        if (
            not isinstance(value, dict)
            or set(value) != {"profile", "installation_id"}
            or identity["created_by"] != "system"
        ):
            raise InstallationProfileError(
                "数据库安装身份标记无效；迁移前拒绝继续。"
            )
        try:
            stored_profile = Profile(value["profile"])
        except (KeyError, TypeError, ValueError):
            raise InstallationProfileError(
                "数据库安装身份标记无效；迁移前拒绝继续。"
            ) from None
        if stored_profile is not expected_profile:
            raise InstallationProfileError(
                f"{PROFILE_MISMATCH_MARKER}: "
                "数据库安装档位不一致；迁移前拒绝修改异源数据库。"
            )
        if value.get("installation_id") != expected_installation_id:
            raise InstallationProfileError(
                "数据库安装身份不一致；迁移前拒绝修改异源数据库。"
            )
        return "existing"

    demo_marker = db.scalar(
        text("SELECT value FROM app_settings WHERE key = :key LIMIT 1"),
        {"key": DEMO_DATASET_MARKER_KEY},
    )
    if (
        expected_profile is Profile.DEMO
        and isinstance(demo_marker, dict)
        and demo_marker.get("dataset_id") == DEMO_DATASET_ID
        and demo_marker.get("mock_only") is True
    ):
        return "legacy-s6-demo"
    raise InstallationProfileError(
        "数据库已有结构但缺少可信安装身份；迁移前拒绝自动归类。"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="数据库不可变安装身份预检")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="只读核验既有身份；缺少标记时失败且不写入",
    )
    mode.add_argument(
        "--pre-migration",
        action="store_true",
        help="迁移前只读核验；仅全新空库或可信旧库可继续",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the fail-closed installation-profile preflight as a one-shot CLI."""
    from app.core.config import settings
    from app.db.session import SessionLocal

    args = _parser().parse_args(argv)
    try:
        with SessionLocal() as db:
            if args.verify_only:
                verify_installation_profile(
                    db,
                    expected_profile=settings.profile,
                    expected_installation_id=settings.installation_id,
                )
            elif args.pre_migration:
                preflight_existing_database(
                    db,
                    expected_profile=settings.profile,
                    expected_installation_id=settings.installation_id,
                )
            else:
                ensure_installation_profile(
                    db,
                    expected_profile=settings.profile,
                    expected_installation_id=settings.installation_id,
                )
                db.commit()
    except InstallationProfileError as exc:
        print(f"[installation-preflight] {exc}", file=sys.stderr)
        return 2
    except Exception:
        print(
            "[installation-preflight] 安装档位预检失败：数据库不可用或结构未就绪。",
            file=sys.stderr,
        )
        return 3

    mode = (
        "迁移前只读核验"
        if args.pre_migration
        else ("只读核验" if args.verify_only else "初始化/核验")
    )
    print(
        f"[installation-preflight] 安装档位{mode}通过：{settings.profile.value}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
