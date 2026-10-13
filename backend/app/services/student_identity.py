"""T18 production student identity, activation and revocable-session services."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.core.config import settings
from app.core.security import (
    create_student_access_token,
    hash_password,
    verify_password,
)
from app.models import (
    College,
    Major,
    OperationReceipt,
    Student,
    StudentAccount,
    StudentActivationMaterial,
    StudentConsent,
    StudentSession,
)

ACCOUNT_PENDING = "pending"
ACCOUNT_ACTIVE = "active"
ACCOUNT_SUSPENDED = "suspended"

# A valid bcrypt hash for a non-credential sentinel.  Missing accounts and
# accounts without a password must still perform one bcrypt verification so
# the login path does not expose activated student numbers through timing.
_DUMMY_PASSWORD_HASH = (
    "$2b$12$J08KiisosxxU4OG4cxLV3e4so0qJ.rYaqogbsK60d7MX6MlOiQK3G"
)
_INVALID_CREDENTIALS_MESSAGE = "学号或密码不正确"
_CONSENT_FIELDS = (
    "recommendation_enabled",
    "growth_profile_enabled",
    "job_action_tracking_enabled",
)


class StudentIdentityError(RuntimeError):
    """Base error for safe, user-visible identity failures."""


class StudentIdentityNotFound(StudentIdentityError):
    pass


class StudentIdentityForbidden(StudentIdentityError):
    pass


class StudentIdentityConflict(StudentIdentityError):
    pass


class ActivationReplayUnavailable(StudentIdentityConflict):
    """The write committed, but one-time plaintext can no longer be returned."""


@dataclass(frozen=True)
class IssuedActivation:
    activation_id: int
    student_id: int
    student_no: str
    name: str
    activation_code: str
    expires_at: datetime


@dataclass(frozen=True)
class ActivationIssueTarget:
    student_id: int
    expected_revision: int
    purpose: str


@dataclass(frozen=True)
class StudentSessionGrant:
    access_token: str
    expires_at: datetime
    session_id: int
    session_jti: str
    account: StudentAccount
    student: Student


def _keyed_digest(namespace: str, value: str) -> str:
    key = (settings.field_encryption_key or settings.jwt_secret).encode("utf-8")
    return hmac.new(
        key,
        f"{namespace}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def activation_code_digest(value: str) -> str:
    return _keyed_digest("student-activation", value)


def session_jti_digest(value: str) -> str:
    return _keyed_digest("student-session", value)


def _consent_state(consent: StudentConsent) -> dict[str, bool]:
    return {
        field: bool(getattr(consent, field))
        for field in _CONSENT_FIELDS
    }


def _consent_audit_state(consent: StudentConsent) -> dict[str, bool | int]:
    return {**_consent_state(consent), "revision": consent.revision}


def _request_hash(payload: dict) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _advisory_lock(db: Session, namespace: str, value: str) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 31))"),
        {"key": f"{namespace}:{value}"},
    )


def _lock_student_credential(db: Session, student_id: int) -> None:
    """Serialize every credential transition for one student.

    The advisory lock is acquired before row locks so issue/recovery and
    activation cannot form a Student/Account/Material reverse lock cycle.
    Multi-student callers acquire these locks in sorted student-id order.
    """
    _advisory_lock(db, "student.credential", str(student_id))


def _account_public_status(account: StudentAccount | None) -> str:
    if account is None:
        return "not_initialized"
    if account.status == ACCOUNT_PENDING:
        return "pending_activation"
    return account.status


def activation_public_status(
    material: StudentActivationMaterial,
    *,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(UTC)
    if material.consumed_at is not None:
        return "consumed"
    if material.revoked_at is not None:
        return "revoked"
    if material.expires_at <= current:
        return "expired"
    return "available"


def list_student_accounts(
    db: Session,
    *,
    search: str | None,
    cohort_year: int | None,
    college_id: int | None,
    status: str | None,
    page: int,
    page_size: int,
) -> dict:
    statement = (
        select(Student, College, Major, StudentAccount)
        .join(College, College.id == Student.college_id)
        .join(Major, Major.id == Student.major_id)
        .outerjoin(StudentAccount, StudentAccount.student_id == Student.id)
    )
    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        statement = statement.where(
            or_(
                Student.student_no.ilike(pattern),
                Student.name.ilike(pattern),
                College.name.ilike(pattern),
                Major.name.ilike(pattern),
            )
        )
    if cohort_year is not None:
        statement = statement.where(Student.cohort_year == cohort_year)
    if college_id is not None:
        statement = statement.where(Student.college_id == college_id)
    if status == "not_initialized":
        statement = statement.where(StudentAccount.id.is_(None))
    elif status == "pending_activation":
        statement = statement.where(StudentAccount.status == ACCOUNT_PENDING)
    elif status in {ACCOUNT_ACTIVE, ACCOUNT_SUSPENDED}:
        statement = statement.where(StudentAccount.status == status)

    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = int(db.scalar(count_statement) or 0)
    rows = db.execute(
        statement.order_by(Student.cohort_year.desc(), Student.student_no, Student.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    student_ids = [student.id for student, _college, _major, _account in rows]
    activation_by_student: dict[int, StudentActivationMaterial] = {}
    if student_ids:
        materials = db.execute(
            select(StudentActivationMaterial)
            .where(StudentActivationMaterial.student_id.in_(student_ids))
            .order_by(
                StudentActivationMaterial.student_id,
                StudentActivationMaterial.created_at.desc(),
                StudentActivationMaterial.id.desc(),
            )
        ).scalars()
        for material in materials:
            activation_by_student.setdefault(material.student_id, material)

    items = []
    for student, college, major, account in rows:
        material = activation_by_student.get(student.id)
        items.append(
            {
                "student_id": student.id,
                "student_no": student.student_no,
                "name": student.name,
                "college_name": college.name,
                "major_name": major.name if major else None,
                "cohort_year": student.cohort_year,
                "status": _account_public_status(account),
                "revision": account.revision if account else 0,
                "activated_at": account.activated_at if account else None,
                "last_login_at": account.last_login_at if account else None,
                "activation_id": material.id if material else None,
                "activation_status": activation_public_status(material) if material else None,
                "activation_expires_at": material.expires_at if material else None,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def issue_activation_materials(
    db: Session,
    *,
    targets: list[ActivationIssueTarget],
    expires_hours: int,
    reason: str,
    idempotency_key: UUID,
    actor: str,
    actor_id: int,
    actor_role: str,
) -> tuple[str, list[IssuedActivation]]:
    key = str(idempotency_key)
    normalized_targets = sorted(targets, key=lambda target: target.student_id)
    normalized_ids = [target.student_id for target in normalized_targets]
    request_hash = _request_hash(
        {
            "targets": [
                {
                    "student_id": target.student_id,
                    "expected_revision": target.expected_revision,
                    "purpose": target.purpose,
                }
                for target in normalized_targets
            ],
            "expires_hours": expires_hours,
            "reason": reason,
        }
    )
    _advisory_lock(db, "student.activation.issue", key)
    receipt = db.execute(
        select(OperationReceipt).where(
            OperationReceipt.namespace == "student.activation.issue",
            OperationReceipt.idempotency_key == key,
        )
    ).scalar_one_or_none()
    if receipt is not None:
        if receipt.request_hash != request_hash:
            raise StudentIdentityConflict("该操作标识已用于另一份学生激活签发内容")
        raise ActivationReplayUnavailable(
            "签发请求已经完成，但一次性激活原文不会再次返回；请先吊销该批材料，再重新签发"
        )

    # It is safe to hold only this request-key lock while hashing: it prevents
    # duplicate work for the same idempotency key but does not block any
    # student/account workflow.  All per-student advisory and row locks are
    # deliberately acquired only after the full batch has been prepared.
    prepared_codes: dict[int, tuple[str, str, str]] = {}
    for target in normalized_targets:
        plain_code = secrets.token_urlsafe(18)
        prepared_codes[target.student_id] = (
            plain_code,
            activation_code_digest(plain_code),
            hash_password(plain_code),
        )

    for student_id in normalized_ids:
        _lock_student_credential(db, student_id)

    students = list(
        db.execute(
            select(Student)
            .where(Student.id.in_(normalized_ids))
            .order_by(Student.id)
            .with_for_update()
        ).scalars()
    )
    if len(students) != len(normalized_ids):
        found = {student.id for student in students}
        missing = [student_id for student_id in normalized_ids if student_id not in found]
        raise StudentIdentityNotFound(f"以下学生不存在：{missing}")

    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=expires_hours)
    correlation_id = f"STU-ACT-{uuid4().hex[:20].upper()}"
    issued: list[IssuedActivation] = []
    targets_by_student = {target.student_id: target for target in normalized_targets}
    for student in students:
        target = targets_by_student[student.id]
        account = db.execute(
            select(StudentAccount)
            .where(StudentAccount.student_id == student.id)
            .with_for_update()
        ).scalar_one_or_none()
        current_revision = account.revision if account is not None else 0
        actual_purpose = (
            "recovery"
            if account is not None and account.password_hash is not None
            else "activation"
        )
        if target.expected_revision != current_revision or target.purpose != actual_purpose:
            raise StudentIdentityConflict(
                f"学生 {student.student_no} 的账户状态已经变化，请刷新列表后重新选择"
            )
        if account is None:
            account = StudentAccount(
                student_id=student.id,
                password_hash=None,
                status=ACCOUNT_PENDING,
                credential_version=1,
                revision=1,
                created_by=actor,
            )
            db.add(account)
            db.flush()
        else:
            # Issuing/replacing credential material is itself a credential
            # state transition.  Advancing the optimistic revision makes a
            # second administrator holding the same stale row fail with 409
            # instead of silently invalidating the first successful handoff.
            account.revision += 1
        if account.status == ACCOUNT_SUSPENDED:
            raise StudentIdentityConflict(
                f"学生 {student.student_no} 的账户已停用；请先恢复账户，再签发激活材料"
            )

        previous = list(
            db.execute(
                select(StudentActivationMaterial)
                .where(
                    StudentActivationMaterial.student_id == student.id,
                    StudentActivationMaterial.consumed_at.is_(None),
                    StudentActivationMaterial.revoked_at.is_(None),
                )
                .order_by(StudentActivationMaterial.id)
                .with_for_update()
            ).scalars()
        )
        revoked_material_ids: list[int] = []
        for material in previous:
            material.revoked_at = now
            material.revoked_by_user_id = actor_id
            material.revoke_reason = "新的激活材料已签发，旧材料自动失效"
            revoked_material_ids.append(material.id)
        if revoked_material_ids:
            record_db(
                db,
                actor=actor,
                actor_id=actor_id,
                actor_role=actor_role,
                source_channel="management",
                correlation_id=correlation_id,
                action="student.activation.auto_revoke",
                object_type="student_account",
                object_id=account.id,
                change_summary={
                    "student_id": student.id,
                    "revoked_material_ids": revoked_material_ids,
                    "revoked_count": len(revoked_material_ids),
                    "reason": "新的激活材料已签发，旧材料自动失效",
                },
            )

        plain_code, code_digest, code_hash = prepared_codes[student.id]
        material = StudentActivationMaterial(
            account_id=account.id,
            student_id=student.id,
            code_digest=code_digest,
            code_hash=code_hash,
            purpose=actual_purpose,
            expires_at=expires_at,
            issued_by_user_id=actor_id,
            issue_reason=reason,
            correlation_id=correlation_id,
            created_by=actor,
        )
        db.add(material)
        db.flush()
        record_db(
            db,
            actor=actor,
            actor_id=actor_id,
            actor_role=actor_role,
            source_channel="management",
            correlation_id=correlation_id,
            action="student.activation.issue",
            object_type="student_activation_material",
            object_id=material.id,
            change_summary={
                "student_id": student.id,
                "purpose": material.purpose,
                "account_revision": account.revision,
                "expires_at": expires_at.isoformat(),
                "reason": reason,
                "plaintext_recorded": False,
            },
        )
        issued.append(
            IssuedActivation(
                activation_id=material.id,
                student_id=student.id,
                student_no=student.student_no,
                name=student.name,
                activation_code=plain_code,
                expires_at=expires_at,
            )
        )

    receipt = OperationReceipt(
        namespace="student.activation.issue",
        idempotency_key=key,
        request_hash=request_hash,
        result_json={
            "correlation_id": correlation_id,
            "activation_ids": [item.activation_id for item in issued],
            "plaintext_recorded": False,
        },
        created_by=actor,
    )
    db.add(receipt)
    db.commit()
    return correlation_id, issued


def revoke_activation_material(
    db: Session,
    *,
    activation_id: int,
    reason: str,
    actor: str,
    actor_id: int,
    actor_role: str,
) -> StudentActivationMaterial:
    material = db.execute(
        select(StudentActivationMaterial)
        .where(StudentActivationMaterial.id == activation_id)
        .with_for_update()
    ).scalar_one_or_none()
    if material is None:
        raise StudentIdentityNotFound("激活材料不存在")
    if material.consumed_at is not None:
        raise StudentIdentityConflict("激活材料已经使用，不能再吊销")
    if material.revoked_at is None:
        now = datetime.now(UTC)
        before = activation_public_status(material, now=now)
        material.revoked_at = now
        material.revoked_by_user_id = actor_id
        material.revoke_reason = reason
        record_db(
            db,
            actor=actor,
            actor_id=actor_id,
            actor_role=actor_role,
            source_channel="management",
            correlation_id=material.correlation_id,
            action="student.activation.revoke",
            object_type="student_activation_material",
            object_id=material.id,
            change_summary={
                "student_id": material.student_id,
                "before": before,
                "after": "revoked",
                "reason": reason,
            },
        )
    db.commit()
    db.refresh(material)
    return material


def _revoke_all_sessions(
    db: Session,
    *,
    account_id: int,
    reason: str,
    now: datetime,
) -> list[int]:
    return list(
        db.execute(
        update(StudentSession)
        .where(
            StudentSession.account_id == account_id,
            StudentSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason=reason)
        .returning(StudentSession.id)
        ).scalars()
    )


def set_student_account_state(
    db: Session,
    *,
    student_id: int,
    target: str,
    expected_revision: int,
    reason: str,
    actor: str,
    actor_id: int,
    actor_role: str,
) -> StudentAccount:
    account = db.execute(
        select(StudentAccount)
        .where(StudentAccount.student_id == student_id)
        .with_for_update()
    ).scalar_one_or_none()
    if account is None:
        raise StudentIdentityNotFound("学生正式账户尚未初始化")
    if account.revision != expected_revision:
        raise StudentIdentityConflict(
            f"账户状态已经更新（当前版本 {account.revision}），请刷新后重试"
        )
    before = _account_public_status(account)
    now = datetime.now(UTC)
    correlation_id = f"STU-STATE-{uuid4().hex[:20].upper()}"
    revoked_session_ids: list[int] = []
    revoked_material_ids: list[int] = []
    if target == ACCOUNT_SUSPENDED:
        if account.status != ACCOUNT_SUSPENDED:
            account.status = ACCOUNT_SUSPENDED
            account.credential_version += 1
            account.revision += 1
            revoked_session_ids = _revoke_all_sessions(
                db,
                account_id=account.id,
                reason="学生账户已由管理员停用",
                now=now,
            )
            available_materials = list(
                db.execute(
                    select(StudentActivationMaterial)
                    .where(
                        StudentActivationMaterial.student_id == student_id,
                        StudentActivationMaterial.consumed_at.is_(None),
                        StudentActivationMaterial.revoked_at.is_(None),
                    )
                    .order_by(StudentActivationMaterial.id)
                    .with_for_update()
                ).scalars()
            )
            for material in available_materials:
                material.revoked_at = now
                material.revoked_by_user_id = actor_id
                material.revoke_reason = "学生账户已由管理员停用"
                revoked_material_ids.append(material.id)
    elif target == ACCOUNT_ACTIVE:
        restored = ACCOUNT_ACTIVE if account.password_hash else ACCOUNT_PENDING
        if account.status != restored:
            account.status = restored
            account.revision += 1
    else:
        raise ValueError("invalid account target")

    after = _account_public_status(account)
    if before != after:
        if revoked_material_ids:
            record_db(
                db,
                actor=actor,
                actor_id=actor_id,
                actor_role=actor_role,
                source_channel="management",
                correlation_id=correlation_id,
                action="student.activation.auto_revoke",
                object_type="student_account",
                object_id=account.id,
                change_summary={
                    "student_id": student_id,
                    "revoked_material_ids": revoked_material_ids,
                    "revoked_count": len(revoked_material_ids),
                    "reason": "学生账户已由管理员停用",
                },
            )
        if revoked_session_ids:
            record_db(
                db,
                actor=actor,
                actor_id=actor_id,
                actor_role=actor_role,
                source_channel="management",
                correlation_id=correlation_id,
                action="student.session.revoke_batch",
                object_type="student_account",
                object_id=account.id,
                change_summary={
                    "student_id": student_id,
                    "session_ids": revoked_session_ids,
                    "revoked_count": len(revoked_session_ids),
                    "reason": "学生账户已由管理员停用",
                },
            )
        record_db(
            db,
            actor=actor,
            actor_id=actor_id,
            actor_role=actor_role,
            source_channel="management",
            correlation_id=correlation_id,
            action=f"student.account.{target}",
            object_type="student_account",
            object_id=account.id,
            change_summary={
                "student_id": student_id,
                "before": before,
                "after": after,
                "reason": reason,
                "revision": account.revision,
            },
        )
    db.commit()
    db.refresh(account)
    return account


def _create_session(
    db: Session,
    *,
    account: StudentAccount,
    student: Student,
) -> StudentSessionGrant:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.jwt_expire_minutes)
    session_jti = str(uuid4())
    row = StudentSession(
        account_id=account.id,
        student_id=student.id,
        jti_digest=session_jti_digest(session_jti),
        expires_at=expires_at,
        created_by=f"student:{student.id}",
    )
    db.add(row)
    db.flush()
    token = create_student_access_token(
        student_id=student.id,
        student_no=student.student_no,
        credential_version=account.credential_version,
        session_jti=session_jti,
        expires_at=expires_at,
    )
    return StudentSessionGrant(
        access_token=token,
        expires_at=expires_at,
        session_id=row.id,
        session_jti=session_jti,
        account=account,
        student=student,
    )


def activate_student_account(
    db: Session,
    *,
    student_no: str,
    activation_code: str,
    password: str,
    consents: dict[str, bool],
) -> StudentSessionGrant:
    digest = activation_code_digest(activation_code)
    candidate = db.execute(
        select(StudentActivationMaterial)
        .where(StudentActivationMaterial.code_digest == digest)
    ).scalar_one_or_none()
    if candidate is None or not verify_password(activation_code, candidate.code_hash):
        raise StudentIdentityNotFound("激活信息无效或已失效")
    # The submitted password is already schema-validated.  Compute its costly
    # bcrypt hash before taking any student credential or row lock; lock-time
    # revalidation below still decides whether this prepared value may commit.
    prepared_password_hash = hash_password(password)

    _lock_student_credential(db, candidate.student_id)
    student = db.execute(
        select(Student)
        .where(Student.id == candidate.student_id)
        .with_for_update()
    ).scalar_one()
    account = db.execute(
        select(StudentAccount)
        .where(StudentAccount.id == candidate.account_id)
        .with_for_update()
    ).scalar_one()
    material = db.execute(
        select(StudentActivationMaterial)
        .where(StudentActivationMaterial.id == candidate.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    now = datetime.now(UTC)
    if student.student_no != student_no.strip():
        raise StudentIdentityNotFound("激活信息无效或已失效")
    state = activation_public_status(material, now=now)
    if state == "expired":
        raise StudentIdentityConflict("一次性激活材料已经过期，请联系学校重新签发")
    if state == "revoked":
        raise StudentIdentityConflict("一次性激活材料已被吊销，请联系学校重新签发")
    if state == "consumed":
        raise StudentIdentityConflict("一次性激活材料已经使用，不能重复激活")
    if account.status == ACCOUNT_SUSPENDED:
        raise StudentIdentityForbidden("学生账户已停用，请联系学校核查")

    is_initial_activation = account.password_hash is None
    before = _account_public_status(account)
    material.consumed_at = now
    account.password_hash = prepared_password_hash
    account.status = ACCOUNT_ACTIVE
    account.credential_version += 1
    account.revision += 1
    account.activated_at = account.activated_at or now
    account.last_login_at = now
    revoked_session_ids = _revoke_all_sessions(
        db,
        account_id=account.id,
        reason="学生完成首次激活或凭据恢复",
        now=now,
    )
    if revoked_session_ids:
        record_db(
            db,
            actor=f"student:{student.id}",
            actor_role="student",
            source_channel="student",
            correlation_id=material.correlation_id,
            action="student.session.revoke_batch",
            object_type="student_account",
            object_id=account.id,
            change_summary={
                "student_id": student.id,
                "session_ids": revoked_session_ids,
                "revoked_count": len(revoked_session_ids),
                "reason": "学生完成首次激活或凭据恢复",
            },
        )
    # Consent is initialized only during first activation.  Recovery rotates a
    # credential and revokes sessions; it has no authority to overwrite the
    # student's independently versioned data-use choices submitted by another
    # tab or an earlier session.
    if material.purpose == "activation":
        consent = db.execute(
            select(StudentConsent)
            .where(StudentConsent.student_id == student.id)
            .with_for_update()
        ).scalar_one_or_none()
        requested_consents = {
            field: bool(consents[field])
            for field in _CONSENT_FIELDS
        }
        consent_before = _consent_state(consent) if consent is not None else None
        consent_audit_before = (
            _consent_audit_state(consent) if consent is not None else None
        )
        if consent is None:
            consent = StudentConsent(
                student_id=student.id,
                **requested_consents,
                created_by=f"student:{student.id}",
            )
            db.add(consent)
        else:
            for field, enabled in requested_consents.items():
                setattr(consent, field, enabled)
            if consent_before != requested_consents:
                consent.revision += 1
        db.flush()
        record_db(
            db,
            actor=f"student:{student.id}",
            actor_role="student",
            source_channel="student",
            correlation_id=material.correlation_id,
            action=(
                "student.consent.initialize"
                if consent_before is None
                else "student.consent.update"
            ),
            object_type="student_consent",
            object_id=consent.id,
            change_summary={
                "student_id": student.id,
                "before": consent_audit_before,
                "after": _consent_audit_state(consent),
            },
        )

    grant = _create_session(db, account=account, student=student)
    authentication_event = "activation" if is_initial_activation else "recovery"
    record_db(
        db,
        actor=f"student:{student.id}",
        actor_role="student",
        source_channel="student",
        correlation_id=material.correlation_id,
        action="student.account.activate" if is_initial_activation else "student.account.recover",
        object_type="student_account",
        object_id=account.id,
        change_summary={
            "student_id": student.id,
            "before": before,
            "after": "active",
            "credential_version": account.credential_version,
            "activation_material_id": material.id,
            "plaintext_recorded": False,
        },
    )
    record_db(
        db,
        actor=f"student:{student.id}",
        actor_role="student",
        source_channel="student",
        correlation_id=material.correlation_id,
        action="student.session.create",
        object_type="student_session",
        object_id=grant.session_id,
        change_summary={
            "student_id": student.id,
            "account_id": account.id,
            "authentication_event": authentication_event,
            "credential_version": account.credential_version,
            "expires_at": grant.expires_at.isoformat(),
        },
    )
    db.commit()
    return grant


def login_student_account(
    db: Session,
    *,
    student_no: str,
    password: str,
) -> StudentSessionGrant:
    # Password verification is intentionally outside a row lock.  Otherwise an
    # unauthenticated caller who merely knows a student number can serialize all
    # login/recovery/disable operations behind one bcrypt calculation per try.
    snapshot = db.execute(
        select(StudentAccount, Student)
        .join(Student, Student.id == StudentAccount.student_id)
        .where(Student.student_no == student_no.strip())
    ).one_or_none()
    stored_hash = (
        snapshot[0].password_hash
        if snapshot is not None and snapshot[0].password_hash is not None
        else _DUMMY_PASSWORD_HASH
    )
    password_matches = verify_password(password, stored_hash)
    if snapshot is None or snapshot[0].password_hash is None or not password_matches:
        raise StudentIdentityNotFound(_INVALID_CREDENTIALS_MESSAGE)
    snapshot_account = snapshot[0]
    snapshot_student_id = snapshot_account.student_id
    snapshot_credential_version = snapshot_account.credential_version
    # A valid login joins the same credential serialization domain as
    # activation/recovery.  Keep the global order credential -> Student ->
    # Account so the session INSERT foreign-key check cannot reverse locks.
    _lock_student_credential(db, snapshot_student_id)
    student = db.execute(
        select(Student)
        .where(Student.id == snapshot_student_id)
        .with_for_update(read=True, key_share=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    account = db.execute(
        select(StudentAccount)
        .where(StudentAccount.id == snapshot_account.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if student is None or account is None:
        raise StudentIdentityNotFound(_INVALID_CREDENTIALS_MESSAGE)
    if (
        account.student_id != student.id
        or account.password_hash != stored_hash
        or account.credential_version != snapshot_credential_version
    ):
        # A recovery/rotation won the race.  Never authenticate with a password
        # verified against the superseded credential; the caller may retry.
        raise StudentIdentityNotFound(_INVALID_CREDENTIALS_MESSAGE)
    if account.status == ACCOUNT_PENDING:
        raise StudentIdentityForbidden("学生账户尚未激活")
    if account.status == ACCOUNT_SUSPENDED:
        raise StudentIdentityForbidden("学生账户已停用，请联系学校核查")
    account.last_login_at = datetime.now(UTC)
    grant = _create_session(db, account=account, student=student)
    record_db(
        db,
        actor=f"student:{student.id}",
        actor_role="student",
        source_channel="student",
        action="student.login",
        object_type="student_session",
        object_id=grant.session_id,
        change_summary={
            "student_id": student.id,
            "account_id": account.id,
            "credential_version": account.credential_version,
            "expires_at": grant.expires_at.isoformat(),
        },
    )
    db.commit()
    return grant


def logout_student_session(
    db: Session,
    *,
    student_id: int,
    session_jti: str,
) -> None:
    digest = session_jti_digest(session_jti)
    session = db.execute(
        select(StudentSession)
        .where(
            StudentSession.student_id == student_id,
            StudentSession.jti_digest == digest,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if session is None:
        raise StudentIdentityNotFound("当前会话不存在")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        session.revoke_reason = "学生主动退出登录"
        record_db(
            db,
            actor=f"student:{student_id}",
            actor_role="student",
            source_channel="student",
            action="student.logout",
            object_type="student_session",
            object_id=session.id,
            change_summary={"reason": session.revoke_reason},
        )
    db.commit()
