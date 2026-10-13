"""Recoverable Demo credential release state machine (hashed DB state only)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.core.config import require_demo_profile, settings
from app.core.security import hash_password, verify_password
from app.d2.enterprise_service import enterprise_token_digest
from app.d2.seed_demo import (
    DEMO_ACTOR,
    DEMO_COMPANY,
    _ensure_demo_submission,
    ensure_d2_demo_story,
)
from app.d2.student_service import binding_code_digest
from app.models import (
    DemoCredentialRelease,
    EnterpriseAccessToken,
    Student,
    StudentBinding,
    User,
)
from app.seed.users import DEMO_ACCOUNTS, seed_demo_users
from app.services.installation import ensure_installation_profile

SCHEMA_VERSION = 1
INFLIGHT_STATUSES = ("staged", "verified", "promoting")
CANDIDATE_AUTH_STATUSES = ("staged", "verified")
_RELEASE_PATTERN = re.compile(r"^demo-[0-9a-f]{32}$")
_CANDIDATE_KEYS = {
    "schema_version",
    "release_id",
    "generated_at",
    "management_accounts",
    "student_binding_code",
    "enterprise_token",
}


class DemoCredentialReleaseError(RuntimeError):
    """Fail-closed credential release error that never contains a secret."""


@dataclass(frozen=True, repr=False)
class DemoCredentialCandidate:
    release_id: str
    generated_at: str
    management_accounts: dict[str, str]
    student_binding_code: str
    enterprise_token: str

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "release_id": self.release_id,
            "generated_at": self.generated_at,
            "management_accounts": {
                key: self.management_accounts[key]
                for key in sorted(self.management_accounts)
            },
            "student_binding_code": self.student_binding_code,
            "enterprise_token": self.enterprise_token,
        }

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def _fsync_directory(path: Path) -> None:
    # Windows does not allow opening a directory through os.open.  The release
    # artifact runs on Linux, where directory fsync is mandatory and tested in CI.
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise DemoCredentialReleaseError("凭据目录无法打开并持久化。") from exc
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            if os.name != "nt":
                raise
    finally:
        os.close(descriptor)


def _validate_secret_path(path: Path) -> Path:
    if path.is_symlink():
        raise DemoCredentialReleaseError("凭据文件不得是符号链接。")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise DemoCredentialReleaseError("凭据目录必须预先存在且不得是符号链接。")
    if os.name != "nt" and stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise DemoCredentialReleaseError("凭据目录权限必须为 0700 或更严格。")
    return path


def _read_protected_json(path: Path) -> dict[str, Any]:
    _validate_secret_path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DemoCredentialReleaseError("凭据文件不存在或无法安全读取。") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DemoCredentialReleaseError("凭据路径必须是普通文件。")
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise DemoCredentialReleaseError("凭据文件权限必须为 0600 或更严格。")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DemoCredentialReleaseError("凭据文件格式无效。") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise DemoCredentialReleaseError("凭据文件必须是 JSON 对象。")
    return payload


def atomic_write_protected_json(
    path: Path, payload: dict[str, Any], *, replace: bool
) -> None:
    path = _validate_secret_path(path)
    if path.exists() and not replace:
        raise DemoCredentialReleaseError("凭据文件已存在，拒绝覆盖。")
    temporary = path.parent / f".{path.name}.tmp.{secrets.token_hex(8)}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        body = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise DemoCredentialReleaseError("凭据文件已由并发流程创建。") from None
            temporary.unlink()
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DemoCredentialReleaseError("凭据文件无法原子持久化。") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_utc(value: object) -> str:
    if not isinstance(value, str):
        raise DemoCredentialReleaseError("凭据生成时间无效。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DemoCredentialReleaseError("凭据生成时间无效。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise DemoCredentialReleaseError("凭据生成时间必须使用 UTC。")
    return value


def candidate_from_payload(payload: dict[str, Any]) -> DemoCredentialCandidate:
    candidate_payload = {key: payload.get(key) for key in _CANDIDATE_KEYS}
    if set(payload) != _CANDIDATE_KEYS:
        raise DemoCredentialReleaseError("候选凭据字段集合无效。")
    if candidate_payload["schema_version"] != SCHEMA_VERSION:
        raise DemoCredentialReleaseError("候选凭据版本不受支持。")
    release_id = candidate_payload["release_id"]
    if not isinstance(release_id, str) or not _RELEASE_PATTERN.fullmatch(release_id):
        raise DemoCredentialReleaseError("候选 release ID 无效。")
    accounts = candidate_payload["management_accounts"]
    if not isinstance(accounts, dict) or set(accounts) != set(DEMO_ACCOUNTS):
        raise DemoCredentialReleaseError("候选管理账号集合无效。")
    if any(not isinstance(value, str) or len(value) < 16 for value in accounts.values()):
        raise DemoCredentialReleaseError("候选管理口令强度不足。")
    binding_code = candidate_payload["student_binding_code"]
    enterprise_token = candidate_payload["enterprise_token"]
    if not isinstance(binding_code, str) or len(binding_code) < 12:
        raise DemoCredentialReleaseError("候选学生绑定码强度不足。")
    if not isinstance(enterprise_token, str) or len(enterprise_token) < 32:
        raise DemoCredentialReleaseError("候选企业令牌强度不足。")
    return DemoCredentialCandidate(
        release_id=release_id,
        generated_at=_parse_utc(candidate_payload["generated_at"]),
        management_accounts=dict(accounts),
        student_binding_code=binding_code,
        enterprise_token=enterprise_token,
    )


def load_candidate_file(path: Path) -> DemoCredentialCandidate:
    return candidate_from_payload(_read_protected_json(path))


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_handoff_candidate(path: Path) -> DemoCredentialCandidate:
    """Load a protected handoff and prove that its plaintext matches its digest."""

    payload = _read_protected_json(path)
    accounts = payload.get("management_accounts")
    if not isinstance(accounts, dict) or set(accounts) != set(DEMO_ACCOUNTS):
        raise DemoCredentialReleaseError("Handoff management account set is invalid.")
    passwords: dict[str, str] = {}
    for username, entry in accounts.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("password"), str):
            raise DemoCredentialReleaseError("Handoff management account is invalid.")
        passwords[username] = entry["password"]
    candidate = candidate_from_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "release_id": payload.get("release_id"),
            "generated_at": payload.get("generated_at"),
            "management_accounts": passwords,
            "student_binding_code": payload.get("student_binding_code"),
            "enterprise_token": payload.get("enterprise_token"),
        }
    )
    if payload.get("candidate_fingerprint") != candidate.fingerprint:
        raise DemoCredentialReleaseError("Handoff candidate fingerprint is invalid.")
    return candidate


def handoff_fingerprint_from_file(
    path: Path, candidate: DemoCredentialCandidate
) -> str:
    bound_candidate = load_handoff_candidate(path)
    if (
        bound_candidate.release_id != candidate.release_id
        or bound_candidate.fingerprint != candidate.fingerprint
    ):
        raise DemoCredentialReleaseError("Handoff does not belong to this release.")
    return _payload_fingerprint(_read_protected_json(path))


def prepare_candidate_file(path: Path) -> DemoCredentialCandidate:
    if path.exists() or path.is_symlink():
        return load_candidate_file(path)
    candidate = DemoCredentialCandidate(
        release_id=f"demo-{secrets.token_hex(16)}",
        generated_at=datetime.now(UTC).isoformat(),
        management_accounts={
            username: secrets.token_urlsafe(24) for username in DEMO_ACCOUNTS
        },
        student_binding_code=secrets.token_urlsafe(18),
        enterprise_token=secrets.token_urlsafe(32),
    )
    try:
        atomic_write_protected_json(path, candidate.payload(), replace=False)
    except DemoCredentialReleaseError as exc:
        if "并发流程" in str(exc) and path.exists() and not path.is_symlink():
            return load_candidate_file(path)
        raise
    return candidate


def _acquire_release_lock(db: Session) -> None:
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"demo-credential:{settings.installation_id}"},
    )


def _release_for_candidate(
    db: Session, candidate: DemoCredentialCandidate
) -> DemoCredentialRelease | None:
    row = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.release_id == candidate.release_id
        )
    ).scalar_one_or_none()
    if row is not None and (
        row.installation_id != settings.installation_id
        or row.candidate_fingerprint != candidate.fingerprint
    ):
        raise DemoCredentialReleaseError("候选文件与数据库发布日志不一致。")
    return row


def stage_demo_credential_release(
    db: Session, candidate: DemoCredentialCandidate
) -> DemoCredentialRelease:
    require_demo_profile("provision_d2_demo")
    ensure_installation_profile(
        db,
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )
    _acquire_release_lock(db)
    existing = _release_for_candidate(db, candidate)
    if existing is not None:
        if existing.status == "aborted":
            raise DemoCredentialReleaseError(
                "An aborted candidate cannot be staged again; prepare a new release."
            )
        return existing
    inflight = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.installation_id == settings.installation_id,
            DemoCredentialRelease.status.in_(INFLIGHT_STATUSES),
        )
    ).scalar_one_or_none()
    if inflight is not None:
        raise DemoCredentialReleaseError("存在另一组未完成候选，拒绝随机轮换。")
    seed_demo_users(db, commit=False)
    story = ensure_d2_demo_story(db)
    access = EnterpriseAccessToken(
        company_name=DEMO_COMPANY,
        token_digest=enterprise_token_digest(candidate.enterprise_token),
        expires_at=datetime.now(UTC) + timedelta(days=30),
        is_active=False,
        created_by=f"credential:{candidate.release_id}",
    )
    db.add(access)
    db.flush()
    active = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.installation_id == settings.installation_id,
            DemoCredentialRelease.status == "active",
        )
    ).scalar_one_or_none()
    release = DemoCredentialRelease(
        release_id=candidate.release_id,
        installation_id=settings.installation_id,
        candidate_fingerprint=candidate.fingerprint,
        status="staged",
        management_password_hashes={
            username: hash_password(candidate.management_accounts[username])
            for username in sorted(candidate.management_accounts)
        },
        verification_json={
            "release_id": candidate.release_id,
            "candidate_fingerprint": candidate.fingerprint,
            "management_logins": {},
        },
        previous_release_id=active.release_id if active else None,
        student_id=story.student.id,
        student_binding_code_digest=binding_code_digest(
            candidate.student_binding_code
        ),
        student_binding_code_hash=hash_password(candidate.student_binding_code),
        enterprise_access_token_id=access.id,
        created_by=DEMO_ACTOR,
    )
    db.add(release)
    record_db(
        db,
        actor=DEMO_ACTOR,
        action="demo_credentials.stage",
        object_type="demo_credential_release",
        object_id=None,
        change_summary={"release_id": release.release_id, "status": "staged"},
    )
    db.commit()
    db.refresh(release)
    return release


def authenticate_candidate_management(
    db: Session, *, username: str, password: str
) -> str | None:
    release = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.installation_id == settings.installation_id,
            DemoCredentialRelease.status.in_(CANDIDATE_AUTH_STATUSES),
        )
    ).scalar_one_or_none()
    if release is None:
        return None
    hashed = release.management_password_hashes.get(username)
    if not isinstance(hashed, str) or not verify_password(password, hashed):
        return None
    return release.release_id


def authenticate_candidate_student(
    db: Session, *, binding_code: str
) -> tuple[str, StudentBinding, Student] | None:
    digest = binding_code_digest(binding_code)
    release = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.installation_id == settings.installation_id,
            DemoCredentialRelease.status.in_(CANDIDATE_AUTH_STATUSES),
            DemoCredentialRelease.student_binding_code_digest == digest,
        )
    ).scalar_one_or_none()
    if release is None or not verify_password(
        binding_code, release.student_binding_code_hash
    ):
        return None
    binding = db.execute(
        select(StudentBinding).where(StudentBinding.student_id == release.student_id)
    ).scalar_one()
    student = db.get(Student, release.student_id)
    if student is None:
        raise DemoCredentialReleaseError("候选学生不存在。")
    return release.release_id, binding, student


def candidate_enterprise_token_allowed(
    db: Session, *, access_token_id: int
) -> str | None:
    release = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.installation_id == settings.installation_id,
            DemoCredentialRelease.status.in_(CANDIDATE_AUTH_STATUSES),
            DemoCredentialRelease.enterprise_access_token_id == access_token_id,
        )
    ).scalar_one_or_none()
    if release is None:
        return None
    return release.release_id


def _release_for_http_receipt(
    db: Session, *, release_id: str
) -> DemoCredentialRelease:
    release = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.release_id == release_id,
            DemoCredentialRelease.installation_id == settings.installation_id,
            DemoCredentialRelease.status.in_(CANDIDATE_AUTH_STATUSES),
        )
    ).scalar_one_or_none()
    if release is None:
        raise DemoCredentialReleaseError("Candidate HTTP receipt release is invalid.")
    return release


def _receipt_evidence(release: DemoCredentialRelease) -> dict[str, Any]:
    evidence = dict(release.verification_json or {})
    evidence.update(
        {
            "release_id": release.release_id,
            "candidate_fingerprint": release.candidate_fingerprint,
        }
    )
    return evidence


def record_candidate_management_http_receipt(
    db: Session,
    *,
    release_id: str,
    username: str,
    surface: str,
) -> None:
    if surface not in {"login", "me"}:
        raise DemoCredentialReleaseError("Management HTTP receipt surface is invalid.")
    release = _release_for_http_receipt(db, release_id=release_id)
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or username not in DEMO_ACCOUNTS:
        raise DemoCredentialReleaseError("Management HTTP receipt identity is invalid.")
    evidence = _receipt_evidence(release)
    logins = dict(evidence.get("management_logins") or {})
    receipt = dict(logins.get(username) or {})
    receipt.update(
        {
            "role": user.role.name,
            "credential_version": user.credential_version,
            f"{surface}_at": datetime.now(UTC).isoformat(),
        }
    )
    logins[username] = receipt
    evidence["management_logins"] = logins
    release.verification_json = evidence


def record_candidate_student_http_receipt(
    db: Session,
    *,
    release_id: str,
    student_id: int,
    surface: str,
) -> None:
    if surface not in {"login", "growth"}:
        raise DemoCredentialReleaseError("Student HTTP receipt surface is invalid.")
    release = _release_for_http_receipt(db, release_id=release_id)
    if release.student_id != student_id:
        raise DemoCredentialReleaseError("Student HTTP receipt identity is invalid.")
    binding = db.execute(
        select(StudentBinding).where(StudentBinding.student_id == student_id)
    ).scalar_one_or_none()
    student = db.get(Student, student_id)
    if binding is None or student is None:
        raise DemoCredentialReleaseError("Student HTTP receipt identity is missing.")
    evidence = _receipt_evidence(release)
    receipt = dict(evidence.get("student_login") or {})
    receipt.update(
        {
            "student_id": student.id,
            "student_no": student.student_no,
            "credential_version": binding.credential_version,
            f"{surface}_at": datetime.now(UTC).isoformat(),
        }
    )
    evidence["student_login"] = receipt
    release.verification_json = evidence


def record_candidate_enterprise_http_receipt(
    db: Session,
    *,
    release_id: str,
    access_token_id: int,
) -> None:
    release = _release_for_http_receipt(db, release_id=release_id)
    if release.enterprise_access_token_id != access_token_id:
        raise DemoCredentialReleaseError("Enterprise HTTP receipt identity is invalid.")
    access = db.get(EnterpriseAccessToken, access_token_id)
    if access is None:
        raise DemoCredentialReleaseError("Enterprise HTTP receipt identity is missing.")
    evidence = _receipt_evidence(release)
    evidence["enterprise_login"] = {
        "access_token_id": access.id,
        "company": access.company_name,
        "expires_at": access.expires_at.isoformat(),
        "session_at": datetime.now(UTC).isoformat(),
    }
    release.verification_json = evidence


def _validated_login_receipts(
    db: Session,
    candidate: DemoCredentialCandidate,
    release: DemoCredentialRelease,
) -> dict[str, Any]:
    """Validate receipts written only by successful candidate login code paths."""

    evidence = release.verification_json
    if (
        not isinstance(evidence, dict)
        or evidence.get("release_id") != release.release_id
        or evidence.get("candidate_fingerprint") != candidate.fingerprint
    ):
        raise DemoCredentialReleaseError("Login receipts are not bound to this release.")
    management_logins = evidence.get("management_logins")
    if not isinstance(management_logins, dict) or set(management_logins) != set(
        DEMO_ACCOUNTS
    ):
        raise DemoCredentialReleaseError("Management login receipts are incomplete.")
    users = db.execute(
        select(User).where(User.username.in_(tuple(DEMO_ACCOUNTS)))
    ).scalars().all()
    by_username = {user.username: user for user in users}
    if set(by_username) != set(DEMO_ACCOUNTS):
        raise DemoCredentialReleaseError("Demo management account metadata is incomplete.")
    management_claims: list[dict[str, Any]] = []
    for username in sorted(DEMO_ACCOUNTS):
        receipt = management_logins.get(username)
        user = by_username[username]
        if (
            not isinstance(receipt, dict)
            or receipt.get("role") != user.role.name
            or receipt.get("credential_version") != user.credential_version
        ):
            raise DemoCredentialReleaseError("Management login receipt is invalid.")
        _parse_utc(receipt.get("login_at"))
        _parse_utc(receipt.get("me_at"))
        management_claims.append({"username": username, **receipt})

    binding = db.execute(
        select(StudentBinding).where(StudentBinding.student_id == release.student_id)
    ).scalar_one_or_none()
    student = db.get(Student, release.student_id)
    student_receipt = evidence.get("student_login")
    if (
        binding is None
        or student is None
        or not isinstance(student_receipt, dict)
        or student_receipt.get("student_id") != release.student_id
        or student_receipt.get("student_no") != student.student_no
        or student_receipt.get("credential_version") != binding.credential_version
    ):
        raise DemoCredentialReleaseError("Student login receipt is invalid.")
    _parse_utc(student_receipt.get("login_at"))
    _parse_utc(student_receipt.get("growth_at"))

    access = db.get(EnterpriseAccessToken, release.enterprise_access_token_id)
    enterprise_receipt = evidence.get("enterprise_login")
    try:
        observed_expiry = datetime.fromisoformat(
            _parse_utc(
                enterprise_receipt.get("expires_at")
                if isinstance(enterprise_receipt, dict)
                else None
            ).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise DemoCredentialReleaseError("Enterprise login receipt expiry is invalid.") from exc
    if (
        access is None
        or not isinstance(enterprise_receipt, dict)
        or enterprise_receipt.get("access_token_id") != access.id
        or enterprise_receipt.get("company") != access.company_name
        or observed_expiry != access.expires_at
        or enterprise_token_digest(candidate.enterprise_token) != access.token_digest
    ):
        raise DemoCredentialReleaseError("Enterprise login receipt is invalid.")
    _parse_utc(enterprise_receipt.get("session_at"))
    return {
        "management_logins": management_claims,
        "student_login": student_receipt,
        "enterprise_login": enterprise_receipt,
        "release_id": release.release_id,
        "candidate_fingerprint": release.candidate_fingerprint,
    }


def mark_demo_credential_release_verified(
    db: Session,
    candidate: DemoCredentialCandidate,
) -> DemoCredentialRelease:
    _acquire_release_lock(db)
    release = _release_for_candidate(db, candidate)
    if release is None:
        raise DemoCredentialReleaseError("候选尚未写入数据库。")
    if release.status in {"verified", "promoting", "active"}:
        return release
    if release.status != "staged":
        raise DemoCredentialReleaseError("候选状态不允许验证。")
    evidence_json = _validated_login_receipts(db, candidate, release)
    release.status = "verified"
    release.verified_at = datetime.now(UTC)
    release.verification_json = evidence_json
    record_db(
        db,
        actor=DEMO_ACTOR,
        action="demo_credentials.verify",
        object_type="demo_credential_release",
        object_id=release.id,
        change_summary={"release_id": release.release_id, "status": "verified"},
    )
    db.commit()
    db.refresh(release)
    return release


def demo_credential_release_status(
    db: Session, candidate: DemoCredentialCandidate
) -> str:
    release = _release_for_candidate(db, candidate)
    if release is None:
        raise DemoCredentialReleaseError("候选尚未写入数据库。")
    return release.status


def demo_credential_release_status_or_none(
    db: Session, candidate: DemoCredentialCandidate
) -> str | None:
    release = _release_for_candidate(db, candidate)
    return release.status if release is not None else None


def inflight_demo_credential_release(db: Session) -> tuple[str, str] | None:
    release = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.installation_id == settings.installation_id,
            DemoCredentialRelease.status.in_(INFLIGHT_STATUSES),
        )
    ).scalar_one_or_none()
    return (release.release_id, release.status) if release is not None else None


def record_demo_credential_handoff(
    db: Session,
    candidate: DemoCredentialCandidate,
    *,
    handoff_path: Path,
) -> DemoCredentialRelease:
    handoff_fingerprint = handoff_fingerprint_from_file(handoff_path, candidate)
    _acquire_release_lock(db)
    release = _release_for_candidate(db, candidate)
    if release is None:
        raise DemoCredentialReleaseError("候选尚未写入数据库。")
    if release.status not in {"verified", "promoting", "active"}:
        raise DemoCredentialReleaseError("候选尚未完成三端验证。")
    if release.handoff_fingerprint not in {None, handoff_fingerprint}:
        raise DemoCredentialReleaseError("交接文件指纹与发布日志不一致。")
    if release.handoff_fingerprint is None:
        release.handoff_fingerprint = handoff_fingerprint
        db.commit()
        db.refresh(release)
    return release


def abort_demo_credential_release(db: Session, *, release_id: str) -> None:
    _acquire_release_lock(db)
    release = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.release_id == release_id,
            DemoCredentialRelease.installation_id == settings.installation_id,
        )
    ).scalar_one_or_none()
    if release is None:
        raise DemoCredentialReleaseError("候选发布不存在。")
    if release.status == "aborted":
        return
    if release.status not in {"staged", "verified"}:
        raise DemoCredentialReleaseError("只有尚未提升的候选可以中止。")
    token = db.get(EnterpriseAccessToken, release.enterprise_access_token_id)
    if token is not None:
        token.is_active = False
    release.status = "aborted"
    record_db(
        db,
        actor=DEMO_ACTOR,
        action="demo_credentials.abort",
        object_type="demo_credential_release",
        object_id=release.id,
        change_summary={"release_id": release.release_id, "status": "aborted"},
    )
    db.commit()


def promote_demo_credential_release(
    db: Session,
    candidate: DemoCredentialCandidate,
    *,
    handoff_path: Path,
) -> DemoCredentialRelease:
    _acquire_release_lock(db)
    release = _release_for_candidate(db, candidate)
    if release is None:
        raise DemoCredentialReleaseError("候选尚未写入数据库。")
    handoff_fingerprint = handoff_fingerprint_from_file(handoff_path, candidate)
    if release.handoff_fingerprint != handoff_fingerprint:
        raise DemoCredentialReleaseError("Handoff file is not bound to this release.")
    if release.status in {"promoting", "active"}:
        return release
    if release.status != "verified":
        raise DemoCredentialReleaseError("候选尚未完成三端验证。")
    if release.handoff_fingerprint is None:
        raise DemoCredentialReleaseError("候选尚未绑定待提升交接文件。")
    token = db.get(EnterpriseAccessToken, release.enterprise_access_token_id)
    if token is None:
        raise DemoCredentialReleaseError("候选企业令牌记录不存在。")
    management_matches = all(
        verify_password(
            candidate.management_accounts[username],
            release.management_password_hashes.get(username, ""),
        )
        for username in DEMO_ACCOUNTS
    )
    if (
        not management_matches
        or candidate.fingerprint != release.candidate_fingerprint
        or binding_code_digest(candidate.student_binding_code)
        != release.student_binding_code_digest
        or not verify_password(
            candidate.student_binding_code, release.student_binding_code_hash
        )
        or enterprise_token_digest(candidate.enterprise_token) != token.token_digest
    ):
        raise DemoCredentialReleaseError("候选文件与数据库哈希不一致。")
    users = db.execute(
        select(User).where(User.username.in_(tuple(DEMO_ACCOUNTS)))
    ).scalars().all()
    by_username = {user.username: user for user in users}
    if set(by_username) != set(DEMO_ACCOUNTS):
        raise DemoCredentialReleaseError("演示管理账号元数据不完整。")
    from app.services.auth_security import revoke_all_management_sessions

    for username, user in by_username.items():
        user.password_hash = release.management_password_hashes[username]
        user.credential_version += 1
        user.password_changed_at = datetime.now(UTC)
        user.is_active = True
        revoke_all_management_sessions(
            db,
            user_id=user.id,
            reason="演示管理凭据发布轮换",
        )
    binding = db.execute(
        select(StudentBinding).where(StudentBinding.student_id == release.student_id)
    ).scalar_one()
    binding.binding_code_digest = release.student_binding_code_digest
    binding.binding_code_hash = release.student_binding_code_hash
    binding.credential_version += 1
    binding.is_active = True
    for access in db.execute(
        select(EnterpriseAccessToken).where(
            EnterpriseAccessToken.company_name == DEMO_COMPANY
        )
    ).scalars():
        access.is_active = access.id == token.id
    token.is_active = True
    previous = db.execute(
        select(DemoCredentialRelease).where(
            DemoCredentialRelease.installation_id == settings.installation_id,
            DemoCredentialRelease.status == "active",
            DemoCredentialRelease.id != release.id,
        )
    ).scalar_one_or_none()
    if previous is not None:
        previous.status = "superseded"
    story = ensure_d2_demo_story(db)
    _ensure_demo_submission(db, job=story.job, access=token)
    release.status = "promoting"
    record_db(
        db,
        actor=DEMO_ACTOR,
        action="demo_credentials.promote",
        object_type="demo_credential_release",
        object_id=release.id,
        change_summary={"release_id": release.release_id, "status": "promoting"},
    )
    db.commit()
    db.refresh(release)
    return release


def finalize_demo_credential_release(
    db: Session,
    candidate: DemoCredentialCandidate,
    *,
    handoff_path: Path,
) -> DemoCredentialRelease:
    _acquire_release_lock(db)
    release = _release_for_candidate(db, candidate)
    if release is None:
        raise DemoCredentialReleaseError("候选尚未写入数据库。")
    handoff_fingerprint = handoff_fingerprint_from_file(handoff_path, candidate)
    if release.handoff_fingerprint != handoff_fingerprint:
        raise DemoCredentialReleaseError("交接文件未与数据库发布日志绑定。")
    if release.status == "promoting":
        release.status = "active"
        release.activated_at = datetime.now(UTC)
        record_db(
            db,
            actor=DEMO_ACTOR,
            action="demo_credentials.activate",
            object_type="demo_credential_release",
            object_id=release.id,
            change_summary={"release_id": release.release_id, "status": "active"},
        )
        db.commit()
        db.refresh(release)
    elif release.status != "active":
        raise DemoCredentialReleaseError("候选不在可完成状态。")
    return release


def remove_protected_file(path: Path) -> None:
    if path.is_symlink():
        raise DemoCredentialReleaseError("拒绝删除符号链接凭据路径。")
    if path.exists():
        path.unlink()
        _fsync_directory(path.parent)
