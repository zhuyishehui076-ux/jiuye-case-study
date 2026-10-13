"""持久化上传/备份命名空间与 PostgreSQL 备份身份守卫。

目录 marker 是资源自身的不可变归属证据。Compose 名称和 Docker labels 只作
辅助；任何跨档位或跨 installation_id 复用必须在业务服务、备份或恢复前失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.core.config import Profile

NamespaceKind = Literal["uploads", "backup"]

NAMESPACE_MARKER_NAME = ".jiuye-namespace.json"
NAMESPACE_LOCK_NAME = ".jiuye-namespace.lock"
NAMESPACE_MARKER_VERSION = 1
BACKUP_MANIFEST_SUFFIX = ".manifest.json"
BACKUP_MANIFEST_VERSION = 1

_NAMESPACE_MARKER_KEYS = {
    "version",
    "kind",
    "profile",
    "installation_id",
}
_BACKUP_MANIFEST_KEYS = {
    "version",
    "kind",
    "profile",
    "installation_id",
    "filename",
    "size_bytes",
    "sha256",
    "created_at_utc",
}
_INSTALLATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_METADATA_BYTES = 16 * 1024


class StorageNamespaceError(RuntimeError):
    """持久化目录或备份文件与预期安装身份不一致。"""


PROFILE_MISMATCH_MARKER = "T15_PROFILE_MISMATCH"


def _validate_identity(
    *,
    kind: NamespaceKind,
    expected_profile: Profile,
    expected_installation_id: str,
) -> None:
    if kind not in {"uploads", "backup"}:
        raise ValueError("kind must be uploads or backup")
    if not isinstance(expected_profile, Profile):
        raise TypeError("expected_profile must be a Profile")
    if not _INSTALLATION_ID_PATTERN.fullmatch(expected_installation_id):
        raise ValueError(
            "expected_installation_id must be 8-128 safe identifier characters"
        )


def _ensure_real_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageNamespaceError("持久化命名空间目录不可创建。") from exc
    if path.is_symlink():
        raise StorageNamespaceError("持久化命名空间目录不得是符号链接。")
    if not path.is_dir():
        raise StorageNamespaceError("持久化命名空间路径不是目录。")


def _verify_real_directory(path: Path) -> None:
    """只读核验现有目录；恢复路径不得创建目录或写锁。"""
    if path.is_symlink():
        raise StorageNamespaceError("持久化命名空间目录不得是符号链接。")
    try:
        if not path.is_dir():
            raise StorageNamespaceError("持久化命名空间路径不存在或不是目录。")
    except OSError as exc:
        raise StorageNamespaceError("持久化命名空间目录不可读取。") from exc


@contextmanager
def _namespace_lock(root: Path, *, timeout_seconds: float) -> Iterator[None]:
    if timeout_seconds <= 0:
        raise ValueError("lock_timeout_seconds must be positive")
    lock_path = root / NAMESPACE_LOCK_NAME
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            lock_path.mkdir(mode=0o700)
            break
        except FileExistsError:
            if lock_path.is_symlink() or not lock_path.is_dir():
                raise StorageNamespaceError("命名空间锁路径无效，拒绝继续。") from None
            if time.monotonic() >= deadline:
                raise StorageNamespaceError("命名空间锁等待超时，拒绝继续。") from None
            time.sleep(0.05)
        except OSError as exc:
            raise StorageNamespaceError("无法获取命名空间锁。") from exc

    try:
        yield
    finally:
        try:
            lock_path.rmdir()
        except OSError as exc:
            raise StorageNamespaceError("命名空间锁无法安全释放。") from exc


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{secrets.token_hex(4)}"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError as exc:
            raise StorageNamespaceError("持久化身份标记权限设置失败。") from exc
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except StorageNamespaceError:
        raise
    except OSError as exc:
        raise StorageNamespaceError("持久化身份标记无法原子写入。") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_exact_json(
    path: Path,
    *,
    expected_keys: set[str],
    label: str,
) -> dict[str, object]:
    if path.is_symlink():
        raise StorageNamespaceError(f"{label}不得是符号链接。")
    try:
        stat = path.stat()
    except OSError as exc:
        raise StorageNamespaceError(f"{label}不可读取。") from exc
    if not path.is_file():
        raise StorageNamespaceError(f"{label}不是普通文件。")
    if stat.st_size > _MAX_METADATA_BYTES:
        raise StorageNamespaceError(f"{label}超过安全大小限制。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageNamespaceError(f"{label}格式无效。") from exc
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise StorageNamespaceError(f"{label}字段不精确，拒绝继续。")
    return payload


def _validate_namespace_payload(
    payload: dict[str, object],
    *,
    kind: NamespaceKind,
    expected_profile: Profile,
    expected_installation_id: str,
) -> None:
    if (
        type(payload["version"]) is not int
        or not isinstance(payload["kind"], str)
        or not isinstance(payload["profile"], str)
        or not isinstance(payload["installation_id"], str)
    ):
        raise StorageNamespaceError("命名空间标记字段类型无效。")
    if payload["version"] != NAMESPACE_MARKER_VERSION:
        raise StorageNamespaceError("命名空间标记版本无效。")
    if payload["kind"] != kind:
        raise StorageNamespaceError("命名空间标记资源类型不一致。")
    if payload["profile"] != expected_profile.value:
        raise StorageNamespaceError(
            f"{PROFILE_MISMATCH_MARKER}: 命名空间标记档位不一致，拒绝跨档复用。"
        )
    if payload["installation_id"] != expected_installation_id:
        raise StorageNamespaceError("命名空间标记安装身份不一致，拒绝跨实例复用。")


def _validate_backup_manifest_payload(payload: dict[str, object]) -> None:
    if (
        type(payload["version"]) is not int
        or not isinstance(payload["kind"], str)
        or not isinstance(payload["profile"], str)
        or not isinstance(payload["installation_id"], str)
        or not isinstance(payload["filename"], str)
        or type(payload["size_bytes"]) is not int
        or not isinstance(payload["sha256"], str)
        or not isinstance(payload["created_at_utc"], str)
    ):
        raise StorageNamespaceError("PostgreSQL 备份身份清单字段类型无效。")
    if payload["size_bytes"] < 0:
        raise StorageNamespaceError("PostgreSQL 备份身份清单大小字段无效。")
    if not _SHA256_PATTERN.fullmatch(payload["sha256"]):
        raise StorageNamespaceError("PostgreSQL 备份身份清单摘要格式无效。")
    try:
        created_at = datetime.fromisoformat(payload["created_at_utc"])
    except ValueError as exc:
        raise StorageNamespaceError(
            "PostgreSQL 备份身份清单创建时间无效。"
        ) from exc
    if created_at.tzinfo is None or created_at.utcoffset() != timedelta(0):
        raise StorageNamespaceError(
            "PostgreSQL 备份身份清单创建时间无效：必须为 UTC。"
        )


def ensure_storage_namespace(
    path: str | Path,
    *,
    kind: NamespaceKind,
    expected_profile: Profile,
    expected_installation_id: str,
    lock_timeout_seconds: float = 5,
) -> dict[str, object]:
    """首次原子认领空目录，或验证其不可变 profile/installation_id。"""
    _validate_identity(
        kind=kind,
        expected_profile=expected_profile,
        expected_installation_id=expected_installation_id,
    )
    root = Path(path)
    _ensure_real_directory(root)
    marker_path = root / NAMESPACE_MARKER_NAME
    expected: dict[str, object] = {
        "version": NAMESPACE_MARKER_VERSION,
        "kind": kind,
        "profile": expected_profile.value,
        "installation_id": expected_installation_id,
    }

    with _namespace_lock(root, timeout_seconds=lock_timeout_seconds):
        if marker_path.exists() or marker_path.is_symlink():
            payload = _read_exact_json(
                marker_path,
                expected_keys=_NAMESPACE_MARKER_KEYS,
                label="命名空间标记",
            )
            _validate_namespace_payload(
                payload,
                kind=kind,
                expected_profile=expected_profile,
                expected_installation_id=expected_installation_id,
            )
            return payload

        unexpected = [entry.name for entry in root.iterdir() if entry.name != NAMESPACE_LOCK_NAME]
        if unexpected:
            raise StorageNamespaceError(
                "未标记的持久化命名空间非空，拒绝自动认领或覆盖。"
            )
        _atomic_write_json(marker_path, expected)
        return expected


def verify_storage_namespace(
    path: str | Path,
    *,
    kind: NamespaceKind,
    expected_profile: Profile,
    expected_installation_id: str,
) -> dict[str, object]:
    """严格只读核验现有 namespace marker，绝不初始化或获取写锁。"""
    _validate_identity(
        kind=kind,
        expected_profile=expected_profile,
        expected_installation_id=expected_installation_id,
    )
    root = Path(path)
    _verify_real_directory(root)
    marker_path = root / NAMESPACE_MARKER_NAME
    if not marker_path.exists() and not marker_path.is_symlink():
        raise StorageNamespaceError("持久化命名空间缺少身份标记，拒绝继续。")
    payload = _read_exact_json(
        marker_path,
        expected_keys=_NAMESPACE_MARKER_KEYS,
        label="命名空间标记",
    )
    _validate_namespace_payload(
        payload,
        kind=kind,
        expected_profile=expected_profile,
        expected_installation_id=expected_installation_id,
    )
    return payload


def preflight_storage_namespace(
    path: str | Path,
    *,
    kind: NamespaceKind,
    expected_profile: Profile,
    expected_installation_id: str,
) -> dict[str, object] | None:
    """启动前只读预检；允许尚未创建/空目录，但绝不创建或认领。"""
    _validate_identity(
        kind=kind,
        expected_profile=expected_profile,
        expected_installation_id=expected_installation_id,
    )
    root = Path(path)
    if root.is_symlink():
        raise StorageNamespaceError("持久化命名空间目录不得是符号链接。")
    try:
        if not root.exists():
            return None
        if not root.is_dir():
            raise StorageNamespaceError("持久化命名空间路径不是目录。")
        marker = root / NAMESPACE_MARKER_NAME
        if marker.exists() or marker.is_symlink():
            return verify_storage_namespace(
                root,
                kind=kind,
                expected_profile=expected_profile,
                expected_installation_id=expected_installation_id,
            )
        if any(root.iterdir()):
            raise StorageNamespaceError(
                "未标记的持久化命名空间非空，启动前只读预检拒绝认领。"
            )
    except StorageNamespaceError:
        raise
    except OSError as exc:
        raise StorageNamespaceError("持久化命名空间目录不可读取。") from exc
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_backup_file(path: str | Path) -> Path:
    backup_path = Path(path)
    if backup_path.is_symlink():
        raise StorageNamespaceError("PostgreSQL 备份文件不得是符号链接。")
    try:
        if not backup_path.is_file():
            raise StorageNamespaceError("PostgreSQL 备份文件不存在或不是普通文件。")
    except OSError as exc:
        raise StorageNamespaceError("PostgreSQL 备份文件不可读取。") from exc
    return backup_path


def _backup_manifest_path(backup_path: Path) -> Path:
    return backup_path.with_name(f"{backup_path.name}{BACKUP_MANIFEST_SUFFIX}")


def create_backup_manifest(
    backup_file: str | Path,
    *,
    expected_profile: Profile,
    expected_installation_id: str,
    lock_timeout_seconds: float = 5,
) -> Path:
    """为本系统产生的 PG dump 原子写入同目录 sidecar 身份与摘要。"""
    backup_path = _validated_backup_file(backup_file)
    ensure_storage_namespace(
        backup_path.parent,
        kind="backup",
        expected_profile=expected_profile,
        expected_installation_id=expected_installation_id,
        lock_timeout_seconds=lock_timeout_seconds,
    )
    manifest_path = _backup_manifest_path(backup_path)
    payload: dict[str, object] = {
        "version": BACKUP_MANIFEST_VERSION,
        "kind": "postgresql-dump",
        "profile": expected_profile.value,
        "installation_id": expected_installation_id,
        "filename": backup_path.name,
        "size_bytes": backup_path.stat().st_size,
        "sha256": _sha256(backup_path),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    with _namespace_lock(backup_path.parent, timeout_seconds=lock_timeout_seconds):
        if manifest_path.exists() or manifest_path.is_symlink():
            raise StorageNamespaceError("PostgreSQL 备份清单已存在，拒绝覆盖。")
        _atomic_write_json(manifest_path, payload)
    return manifest_path


def verify_backup_manifest(
    backup_file: str | Path,
    *,
    expected_profile: Profile,
    expected_installation_id: str,
    lock_timeout_seconds: float = 5,
) -> dict[str, object]:
    """在恢复前验证目录归属、sidecar 身份、文件名、大小与 SHA256。"""
    if lock_timeout_seconds <= 0:
        raise ValueError("lock_timeout_seconds must be positive")
    backup_path = _validated_backup_file(backup_file)
    verify_storage_namespace(
        backup_path.parent,
        kind="backup",
        expected_profile=expected_profile,
        expected_installation_id=expected_installation_id,
    )
    manifest_path = _backup_manifest_path(backup_path)
    if not manifest_path.exists() and not manifest_path.is_symlink():
        raise StorageNamespaceError("PostgreSQL 备份缺少身份清单，拒绝恢复。")
    payload = _read_exact_json(
        manifest_path,
        expected_keys=_BACKUP_MANIFEST_KEYS,
        label="PostgreSQL 备份身份清单",
    )
    _validate_backup_manifest_payload(payload)
    if payload["version"] != BACKUP_MANIFEST_VERSION:
        raise StorageNamespaceError("PostgreSQL 备份身份清单版本无效。")
    if payload["kind"] != "postgresql-dump":
        raise StorageNamespaceError("PostgreSQL 备份身份清单类型无效。")
    if payload["profile"] != expected_profile.value:
        raise StorageNamespaceError(
            f"{PROFILE_MISMATCH_MARKER}: PostgreSQL 备份档位不一致，拒绝跨档恢复。"
        )
    if payload["installation_id"] != expected_installation_id:
        raise StorageNamespaceError(
            "PostgreSQL 备份安装身份不一致，拒绝跨实例恢复。"
        )
    if payload["filename"] != backup_path.name:
        raise StorageNamespaceError("PostgreSQL 备份文件名与身份清单不一致。")
    if payload["size_bytes"] != backup_path.stat().st_size:
        raise StorageNamespaceError("PostgreSQL 备份大小与身份清单不一致。")
    if payload["sha256"] != _sha256(backup_path):
        raise StorageNamespaceError("PostgreSQL 备份摘要不一致，文件可能已损坏或篡改。")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验持久化命名空间和备份身份")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure_parser = subparsers.add_parser("ensure")
    ensure_parser.add_argument("--kind", required=True, choices=("uploads", "backup"))
    ensure_parser.add_argument("--path", required=True)

    verify_namespace_parser = subparsers.add_parser("verify")
    verify_namespace_parser.add_argument(
        "--kind", required=True, choices=("uploads", "backup")
    )
    verify_namespace_parser.add_argument("--path", required=True)

    preflight_namespace_parser = subparsers.add_parser("preflight")
    preflight_namespace_parser.add_argument(
        "--kind", required=True, choices=("uploads", "backup")
    )
    preflight_namespace_parser.add_argument("--path", required=True)

    create_parser = subparsers.add_parser("create-backup-manifest")
    create_parser.add_argument("--file", required=True)

    verify_parser = subparsers.add_parser("verify-backup-manifest")
    verify_parser.add_argument("--file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 输出只包含结果类别，不输出密钥、数据库 URL 或 installation_id。"""
    try:
        args = _parser().parse_args(argv)
        from app.core.config import settings

        if args.command == "ensure":
            ensure_storage_namespace(
                args.path,
                kind=args.kind,
                expected_profile=settings.profile,
                expected_installation_id=settings.installation_id,
            )
            print(f"[storage-preflight] {args.kind} 命名空间校验通过")
        elif args.command == "verify":
            verify_storage_namespace(
                args.path,
                kind=args.kind,
                expected_profile=settings.profile,
                expected_installation_id=settings.installation_id,
            )
            print(f"[storage-preflight] {args.kind} 命名空间只读核验通过")
        elif args.command == "preflight":
            preflight_storage_namespace(
                args.path,
                kind=args.kind,
                expected_profile=settings.profile,
                expected_installation_id=settings.installation_id,
            )
            print(f"[storage-preflight] {args.kind} 命名空间启动前只读预检通过")
        elif args.command == "create-backup-manifest":
            create_backup_manifest(
                args.file,
                expected_profile=settings.profile,
                expected_installation_id=settings.installation_id,
            )
            print("[storage-preflight] PostgreSQL 备份身份清单已创建")
        else:
            verify_backup_manifest(
                args.file,
                expected_profile=settings.profile,
                expected_installation_id=settings.installation_id,
            )
            print("[storage-preflight] PostgreSQL 备份身份校验通过")
    except StorageNamespaceError as exc:
        print(f"[storage-preflight] {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("[storage-preflight] 命名空间预检失败。", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
