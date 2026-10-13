"""T19 production enterprise identity, activation and revocable sessions."""

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
from app.core.crypto import encrypt_field
from app.core.security import (
    create_enterprise_access_token,
    hash_password,
    verify_password,
)
from app.models import (
    AuditLog,
    EnterpriseAccount,
    EnterpriseActivationMaterial,
    EnterpriseOrganization,
    EnterpriseSession,
    OperationReceipt,
)

ACCOUNT_PENDING = "pending"
ACCOUNT_ACTIVE = "active"
ACCOUNT_SUSPENDED = "suspended"
ORG_PENDING = "pending"
ORG_VERIFIED = "verified"
ORG_SUSPENDED = "suspended"

_DUMMY_PASSWORD_HASH = (
    "$2b$12$J08KiisosxxU4OG4cxLV3e4so0qJ.rYaqogbsK60d7MX6MlOiQK3G"
)
_INVALID_CREDENTIALS_MESSAGE = "企业账号或密码不正确"


class EnterpriseIdentityError(RuntimeError):
    pass


class EnterpriseIdentityNotFound(EnterpriseIdentityError):
    pass


class EnterpriseIdentityForbidden(EnterpriseIdentityError):
    pass


class EnterpriseIdentityConflict(EnterpriseIdentityError):
    pass


class EnterpriseActivationReplayUnavailable(EnterpriseIdentityConflict):
    """The write committed; the one-time plaintext cannot be returned again."""


@dataclass(frozen=True)
class EnterpriseAccountInitTarget:
    organization_id: int
    account_id: int | None
    expected_revision: int
    purpose: str


@dataclass(frozen=True)
class IssuedEnterpriseActivation:
    activation_id: int
    organization_id: int
    account_id: int
    username: str
    company: str
    activation_code: str
    expires_at: datetime


@dataclass(frozen=True)
class EnterpriseSessionGrant:
    access_token: str
    expires_at: datetime
    session_id: int
    session_jti: str
    account: EnterpriseAccount
    organization: EnterpriseOrganization


def _keyed_digest(namespace: str, value: str) -> str:
    key = (settings.field_encryption_key or settings.jwt_secret).encode("utf-8")
    return hmac.new(key, f"{namespace}:{value}".encode(), hashlib.sha256).hexdigest()


def enterprise_activation_code_digest(value: str) -> str:
    return _keyed_digest("enterprise-activation", value)


def enterprise_session_jti_digest(value: str) -> str:
    return _keyed_digest("enterprise-session", value)


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
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 57))"),
        {"key": f"{namespace}:{value}"},
    )


def _lock_enterprise_credential(db: Session, organization_id: int) -> None:
    _advisory_lock(db, "enterprise.credential", str(organization_id))


def _account_public_status(account: EnterpriseAccount | None) -> str:
    if account is None:
        return "not_initialized"
    if account.status == ACCOUNT_PENDING:
        return "pending_activation"
    return account.status


def activation_public_status(
    material: EnterpriseActivationMaterial,
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


def list_enterprise_accounts(
    db: Session,
    *,
    search: str | None,
    status: str | None,
    page: int,
    page_size: int,
) -> dict:
    statement = select(EnterpriseOrganization, EnterpriseAccount).outerjoin(
        EnterpriseAccount,
        EnterpriseAccount.organization_id == EnterpriseOrganization.id,
    )
    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        statement = statement.where(
            or_(
                EnterpriseOrganization.legal_name.ilike(pattern),
                EnterpriseOrganization.short_name.ilike(pattern),
                EnterpriseOrganization.credit_code.ilike(pattern),
                EnterpriseAccount.username.ilike(pattern),
            )
        )
    if status == "not_initialized":
        statement = statement.where(EnterpriseAccount.id.is_(None))
    elif status == "pending_activation":
        statement = statement.where(EnterpriseAccount.status == ACCOUNT_PENDING)
    elif status in {ACCOUNT_ACTIVE, ACCOUNT_SUSPENDED}:
        statement = statement.where(EnterpriseAccount.status == status)

    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = db.execute(
        statement.order_by(EnterpriseOrganization.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    account_ids = [account.id for _org, account in rows if account is not None]
    material_by_account: dict[int, EnterpriseActivationMaterial] = {}
    if account_ids:
        materials = db.execute(
            select(EnterpriseActivationMaterial)
            .where(EnterpriseActivationMaterial.account_id.in_(account_ids))
            .order_by(
                EnterpriseActivationMaterial.account_id,
                EnterpriseActivationMaterial.created_at.desc(),
                EnterpriseActivationMaterial.id.desc(),
            )
        ).scalars()
        for material in materials:
            material_by_account.setdefault(material.account_id, material)
    items = []
    for organization, account in rows:
        material = material_by_account.get(account.id) if account else None
        items.append(
            {
                "organization_id": organization.id,
                "legal_name": organization.legal_name,
                "short_name": organization.short_name,
                "credit_code": organization.credit_code,
                "organization_status": organization.status,
                "organization_revision": organization.revision,
                "account_id": account.id if account else None,
                "username": account.username if account else None,
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


def upsert_enterprise_organization(
    db: Session,
    *,
    legal_name: str,
    credit_code: str,
    username: str,
    short_name: str | None,
    industry: str | None,
    scale: str | None,
    city: str | None,
    address: str | None,
    website: str | None,
    summary: str | None,
    contact_name: str | None,
    contact_phone: str | None,
    idempotency_key: UUID,
    actor: str,
    actor_id: int,
    actor_role: str,
) -> dict:
    key = str(idempotency_key)
    request_hash = _request_hash(
        {
            "legal_name": legal_name,
            "credit_code": credit_code,
            "username": username,
            "short_name": short_name,
            "industry": industry,
            "scale": scale,
            "city": city,
            "address": address,
            "website": website,
            "summary": summary,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
        }
    )
    _advisory_lock(db, "enterprise.organization.upsert", key)
    receipt = db.execute(
        select(OperationReceipt).where(
            OperationReceipt.namespace == "enterprise.organization.upsert",
            OperationReceipt.idempotency_key == key,
        )
    ).scalar_one_or_none()
    if receipt is not None:
        if receipt.request_hash != request_hash:
            raise EnterpriseIdentityConflict("该操作标识已用于另一份企业资料")
        return receipt.result_json

    organization = db.execute(
        select(EnterpriseOrganization)
        .where(EnterpriseOrganization.credit_code == credit_code)
        .with_for_update()
    ).scalar_one_or_none()
    now_state = {
        "legal_name": legal_name,
        "short_name": short_name,
        "industry": industry,
        "scale": scale,
        "city": city,
        "address": address,
        "website": website,
        "summary": summary,
        "contact_name": contact_name,
    }
    if organization is None:
        organization = EnterpriseOrganization(
            legal_name=legal_name,
            short_name=short_name,
            credit_code=credit_code,
            industry=industry,
            scale=scale,
            city=city,
            address=address,
            website=website,
            summary=summary,
            contact_name=contact_name,
            contact_phone_encrypted=encrypt_field(contact_phone) if contact_phone else None,
            status=ORG_VERIFIED,
            created_by=actor,
        )
        db.add(organization)
        db.flush()
        before = None
    else:
        before = {
            "legal_name": organization.legal_name,
            "short_name": organization.short_name,
            "industry": organization.industry,
            "scale": organization.scale,
            "city": organization.city,
            "address": organization.address,
            "website": organization.website,
            "summary": organization.summary,
            "contact_name": organization.contact_name,
            "status": organization.status,
        }
        for field, value in now_state.items():
            setattr(organization, field, value)
        if contact_phone:
            organization.contact_phone_encrypted = encrypt_field(contact_phone)
        if organization.status == ORG_PENDING:
            organization.status = ORG_VERIFIED
        organization.revision += 1

    account = db.execute(
        select(EnterpriseAccount)
        .where(EnterpriseAccount.organization_id == organization.id)
        .with_for_update()
    ).scalar_one_or_none()
    if account is None:
        account = EnterpriseAccount(
            organization_id=organization.id,
            username=username,
            status=ACCOUNT_PENDING,
            created_by=actor,
        )
        db.add(account)
        db.flush()
    elif account.username != username:
        conflict = db.execute(
            select(EnterpriseAccount.id).where(EnterpriseAccount.username == username)
        ).scalar_one_or_none()
        if conflict is not None:
            raise EnterpriseIdentityConflict("企业登录名已被其他企业使用")
        account.username = username
        account.revision += 1

    result = {
        "organization_id": organization.id,
        "account_id": account.id,
        "legal_name": organization.legal_name,
        "username": account.username,
        "organization_revision": organization.revision,
        "account_revision": account.revision,
    }
    correlation_id = f"ENT-ORG-{uuid4().hex[:20].upper()}"
    record_db(
        db,
        actor=actor,
        actor_id=actor_id,
        actor_role=actor_role,
        source_channel="management",
        correlation_id=correlation_id,
        action="enterprise.organization.upsert",
        object_type="enterprise_organization",
        object_id=organization.id,
        change_summary={"before": before, "after": result},
    )
    db.add(
        OperationReceipt(
            namespace="enterprise.organization.upsert",
            idempotency_key=key,
            request_hash=request_hash,
            result_json=result,
            created_by=actor,
        )
    )
    db.commit()
    return result


def issue_activation_materials(
    db: Session,
    *,
    targets: list[EnterpriseAccountInitTarget],
    expires_hours: int,
    reason: str,
    idempotency_key: UUID,
    actor: str,
    actor_id: int,
    actor_role: str,
) -> tuple[str, list[IssuedEnterpriseActivation]]:
    key = str(idempotency_key)
    normalized_targets = sorted(targets, key=lambda target: target.organization_id)
    request_hash = _request_hash(
        {
            "targets": [
                {
                    "organization_id": target.organization_id,
                    "account_id": target.account_id,
                    "expected_revision": target.expected_revision,
                    "purpose": target.purpose,
                }
                for target in normalized_targets
            ],
            "expires_hours": expires_hours,
            "reason": reason,
        }
    )
    _advisory_lock(db, "enterprise.activation.issue", key)
    receipt = db.execute(
        select(OperationReceipt).where(
            OperationReceipt.namespace == "enterprise.activation.issue",
            OperationReceipt.idempotency_key == key,
        )
    ).scalar_one_or_none()
    if receipt is not None:
        if receipt.request_hash != request_hash:
            raise EnterpriseIdentityConflict("该操作标识已用于另一份企业激活签发内容")
        raise EnterpriseActivationReplayUnavailable(
            "签发请求已经完成，但一次性激活原文不会再次返回；请先吊销旧材料再重新签发"
        )

    prepared_codes: dict[int, tuple[str, str, str]] = {}
    for target in normalized_targets:
        plain_code = secrets.token_urlsafe(18)
        prepared_codes[target.organization_id] = (
            plain_code,
            enterprise_activation_code_digest(plain_code),
            hash_password(plain_code),
        )

    for target in normalized_targets:
        _lock_enterprise_credential(db, target.organization_id)

    organizations = list(
        db.execute(
            select(EnterpriseOrganization)
            .where(
                EnterpriseOrganization.id.in_(
                    [target.organization_id for target in normalized_targets]
                )
            )
            .order_by(EnterpriseOrganization.id)
            .with_for_update()
        ).scalars()
    )
    if len(organizations) != len(normalized_targets):
        raise EnterpriseIdentityNotFound("企业组织不存在")

    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=expires_hours)
    correlation_id = f"ENT-ACT-{uuid4().hex[:20].upper()}"
    issued: list[IssuedEnterpriseActivation] = []
    targets_by_org = {target.organization_id: target for target in normalized_targets}
    for organization in organizations:
        target = targets_by_org[organization.id]
        if organization.status == ORG_SUSPENDED:
            raise EnterpriseIdentityConflict("企业组织已停用，不能签发激活材料")
        account = db.execute(
            select(EnterpriseAccount)
            .where(EnterpriseAccount.organization_id == organization.id)
            .with_for_update()
        ).scalar_one_or_none()
        if account is None or (target.account_id is not None and account.id != target.account_id):
            raise EnterpriseIdentityNotFound("企业账号不存在")
        current_revision = account.revision
        actual_purpose = "recovery" if account.password_hash is not None else "activation"
        if current_revision != target.expected_revision or actual_purpose != target.purpose:
            raise EnterpriseIdentityConflict("企业账号状态已变化，请刷新后重新签发")
        if account.status == ACCOUNT_SUSPENDED:
            raise EnterpriseIdentityConflict("企业账号已停用，不能签发激活材料")
        account.revision += 1

        previous = list(
            db.execute(
                select(EnterpriseActivationMaterial)
                .where(
                    EnterpriseActivationMaterial.account_id == account.id,
                    EnterpriseActivationMaterial.consumed_at.is_(None),
                    EnterpriseActivationMaterial.revoked_at.is_(None),
                )
                .order_by(EnterpriseActivationMaterial.id)
                .with_for_update()
            ).scalars()
        )
        for material in previous:
            material.revoked_at = now
            material.revoked_by_user_id = actor_id
            material.revoke_reason = "新的企业激活材料已签发，旧材料自动失效"

        plain_code, code_digest, code_hash = prepared_codes[organization.id]
        material = EnterpriseActivationMaterial(
            account_id=account.id,
            organization_id=organization.id,
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
            action="enterprise.activation.issue",
            object_type="enterprise_activation_material",
            object_id=material.id,
            change_summary={
                "organization_id": organization.id,
                "account_id": account.id,
                "purpose": material.purpose,
                "account_revision": account.revision,
                "expires_at": expires_at.isoformat(),
                "plaintext_recorded": False,
            },
        )
        issued.append(
            IssuedEnterpriseActivation(
                activation_id=material.id,
                organization_id=organization.id,
                account_id=account.id,
                username=account.username,
                company=organization.short_name or organization.legal_name,
                activation_code=plain_code,
                expires_at=expires_at,
            )
        )

    db.add(
        OperationReceipt(
            namespace="enterprise.activation.issue",
            idempotency_key=key,
            request_hash=request_hash,
            result_json={
                "correlation_id": correlation_id,
                "activation_ids": [item.activation_id for item in issued],
                "plaintext_recorded": False,
            },
            created_by=actor,
        )
    )
    db.commit()
    return correlation_id, issued


def _revoke_all_sessions(
    db: Session,
    *,
    account_id: int,
    reason: str,
    now: datetime,
) -> list[int]:
    return list(
        db.execute(
            update(EnterpriseSession)
            .where(
                EnterpriseSession.account_id == account_id,
                EnterpriseSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoke_reason=reason)
            .returning(EnterpriseSession.id)
        ).scalars()
    )


def _create_session(
    db: Session,
    *,
    account: EnterpriseAccount,
    organization: EnterpriseOrganization,
) -> EnterpriseSessionGrant:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.jwt_expire_minutes)
    session_jti = str(uuid4())
    row = EnterpriseSession(
        account_id=account.id,
        organization_id=organization.id,
        jti_digest=enterprise_session_jti_digest(session_jti),
        expires_at=expires_at,
        created_by=f"enterprise:{organization.id}",
    )
    db.add(row)
    db.flush()
    token = create_enterprise_access_token(
        organization_id=organization.id,
        account_id=account.id,
        username=account.username,
        credential_version=account.credential_version,
        session_jti=session_jti,
        expires_at=expires_at,
    )
    return EnterpriseSessionGrant(
        access_token=token,
        expires_at=expires_at,
        session_id=row.id,
        session_jti=session_jti,
        account=account,
        organization=organization,
    )


def activate_enterprise_account(
    db: Session,
    *,
    username: str,
    activation_code: str,
    password: str,
) -> EnterpriseSessionGrant:
    digest = enterprise_activation_code_digest(activation_code)
    candidate = db.execute(
        select(EnterpriseActivationMaterial).where(
            EnterpriseActivationMaterial.code_digest == digest
        )
    ).scalar_one_or_none()
    if candidate is None or not verify_password(activation_code, candidate.code_hash):
        raise EnterpriseIdentityNotFound("激活信息无效或已失效")
    prepared_password_hash = hash_password(password)

    _lock_enterprise_credential(db, candidate.organization_id)
    organization = db.execute(
        select(EnterpriseOrganization)
        .where(EnterpriseOrganization.id == candidate.organization_id)
        .with_for_update()
    ).scalar_one()
    account = db.execute(
        select(EnterpriseAccount)
        .where(EnterpriseAccount.id == candidate.account_id)
        .with_for_update()
    ).scalar_one()
    material = db.execute(
        select(EnterpriseActivationMaterial)
        .where(EnterpriseActivationMaterial.id == candidate.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    now = datetime.now(UTC)
    if account.username != username.strip():
        raise EnterpriseIdentityNotFound("激活信息无效或已失效")
    state = activation_public_status(material, now=now)
    if state == "expired":
        raise EnterpriseIdentityConflict("一次性企业激活材料已经过期，请联系学校重新签发")
    if state == "revoked":
        raise EnterpriseIdentityConflict("一次性企业激活材料已被吊销，请联系学校重新签发")
    if state == "consumed":
        raise EnterpriseIdentityConflict("一次性企业激活材料已经使用，不能重复激活")
    if organization.status == ORG_SUSPENDED or account.status == ACCOUNT_SUSPENDED:
        raise EnterpriseIdentityForbidden("企业账号已停用，请联系学校核查")

    is_initial_activation = account.password_hash is None
    before = _account_public_status(account)
    material.consumed_at = now
    account.password_hash = prepared_password_hash
    account.status = ACCOUNT_ACTIVE
    account.credential_version += 1
    account.revision += 1
    account.activated_at = account.activated_at or now
    account.last_login_at = now
    _revoke_all_sessions(
        db,
        account_id=account.id,
        reason="企业完成首次激活或凭据恢复",
        now=now,
    )
    grant = _create_session(db, account=account, organization=organization)
    record_db(
        db,
        actor=f"enterprise:{organization.id}",
        actor_role="enterprise",
        source_channel="enterprise",
        correlation_id=material.correlation_id,
        action="enterprise.account.activate" if is_initial_activation else "enterprise.account.recover",
        object_type="enterprise_account",
        object_id=account.id,
        change_summary={
            "organization_id": organization.id,
            "before": before,
            "after": "active",
            "credential_version": account.credential_version,
            "activation_material_id": material.id,
            "plaintext_recorded": False,
        },
    )
    record_db(
        db,
        actor=f"enterprise:{organization.id}",
        actor_role="enterprise",
        source_channel="enterprise",
        correlation_id=material.correlation_id,
        action="enterprise.session.create",
        object_type="enterprise_session",
        object_id=grant.session_id,
        change_summary={
            "organization_id": organization.id,
            "account_id": account.id,
            "expires_at": grant.expires_at.isoformat(),
        },
    )
    db.commit()
    return grant


def login_enterprise_account(
    db: Session,
    *,
    username: str,
    password: str,
) -> EnterpriseSessionGrant:
    snapshot = db.execute(
        select(EnterpriseAccount, EnterpriseOrganization)
        .join(
            EnterpriseOrganization,
            EnterpriseOrganization.id == EnterpriseAccount.organization_id,
        )
        .where(EnterpriseAccount.username == username.strip())
    ).one_or_none()
    stored_hash = (
        snapshot[0].password_hash
        if snapshot is not None and snapshot[0].password_hash is not None
        else _DUMMY_PASSWORD_HASH
    )
    password_matches = verify_password(password, stored_hash)
    if snapshot is None or snapshot[0].password_hash is None or not password_matches:
        raise EnterpriseIdentityNotFound(_INVALID_CREDENTIALS_MESSAGE)
    snapshot_account = snapshot[0]
    _lock_enterprise_credential(db, snapshot_account.organization_id)
    organization = db.execute(
        select(EnterpriseOrganization)
        .where(EnterpriseOrganization.id == snapshot_account.organization_id)
        .with_for_update(read=True, key_share=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    account = db.execute(
        select(EnterpriseAccount)
        .where(EnterpriseAccount.id == snapshot_account.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        organization is None
        or account is None
        or account.password_hash != stored_hash
        or account.credential_version != snapshot_account.credential_version
    ):
        raise EnterpriseIdentityNotFound(_INVALID_CREDENTIALS_MESSAGE)
    if organization.status == ORG_SUSPENDED or account.status == ACCOUNT_SUSPENDED:
        raise EnterpriseIdentityForbidden("企业账号已停用，请联系学校核查")
    if account.status == ACCOUNT_PENDING:
        raise EnterpriseIdentityForbidden("企业账号尚未激活")
    account.last_login_at = datetime.now(UTC)
    grant = _create_session(db, account=account, organization=organization)
    record_db(
        db,
        actor=f"enterprise:{organization.id}",
        actor_role="enterprise",
        source_channel="enterprise",
        action="enterprise.login",
        object_type="enterprise_session",
        object_id=grant.session_id,
        change_summary={
            "organization_id": organization.id,
            "account_id": account.id,
            "expires_at": grant.expires_at.isoformat(),
        },
    )
    db.commit()
    return grant


def logout_enterprise_session(
    db: Session,
    *,
    organization_id: int,
    session_jti: str,
) -> None:
    row = db.execute(
        select(EnterpriseSession)
        .where(
            EnterpriseSession.organization_id == organization_id,
            EnterpriseSession.jti_digest == enterprise_session_jti_digest(session_jti),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise EnterpriseIdentityNotFound("当前企业会话不存在")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revoke_reason = "企业主动退出登录"
        record_db(
            db,
            actor=f"enterprise:{organization_id}",
            actor_role="enterprise",
            source_channel="enterprise",
            action="enterprise.logout",
            object_type="enterprise_session",
            object_id=row.id,
            change_summary={"reason": row.revoke_reason},
        )
    db.commit()


def set_enterprise_account_state(
    db: Session,
    *,
    account_id: int,
    target: str,
    expected_revision: int,
    reason: str,
    actor: str,
    actor_id: int,
    actor_role: str,
) -> EnterpriseAccount:
    account = db.execute(
        select(EnterpriseAccount)
        .where(EnterpriseAccount.id == account_id)
        .with_for_update()
    ).scalar_one_or_none()
    if account is None:
        raise EnterpriseIdentityNotFound("企业账号不存在")
    if account.revision != expected_revision:
        raise EnterpriseIdentityConflict("企业账号状态已更新，请刷新后重试")
    _lock_enterprise_credential(db, account.organization_id)
    before = _account_public_status(account)
    now = datetime.now(UTC)
    revoked_session_ids: list[int] = []
    if target == ACCOUNT_SUSPENDED and account.status != ACCOUNT_SUSPENDED:
        account.status = ACCOUNT_SUSPENDED
        account.credential_version += 1
        account.revision += 1
        revoked_session_ids = _revoke_all_sessions(
            db,
            account_id=account.id,
            reason="企业账号已由管理员停用",
            now=now,
        )
    elif target == ACCOUNT_ACTIVE:
        restored = ACCOUNT_ACTIVE if account.password_hash else ACCOUNT_PENDING
        if account.status != restored:
            account.status = restored
            account.revision += 1
    else:
        raise ValueError("invalid account target")

    after = _account_public_status(account)
    if before != after:
        record_db(
            db,
            actor=actor,
            actor_id=actor_id,
            actor_role=actor_role,
            source_channel="management",
            correlation_id=f"ENT-STATE-{uuid4().hex[:20].upper()}",
            action=f"enterprise.account.{target}",
            object_type="enterprise_account",
            object_id=account.id,
            change_summary={
                "organization_id": account.organization_id,
                "before": before,
                "after": after,
                "reason": reason,
                "revision": account.revision,
                "revoked_session_ids": revoked_session_ids,
            },
        )
    db.commit()
    db.refresh(account)
    return account


def assert_no_replayed_activation_plaintext(
    db: Session,
    *,
    correlation_id: str,
    plain_code: str,
) -> None:
    """Test helper proving one-time plaintext never persists in audit rows."""
    rows = db.execute(
        select(AuditLog).where(AuditLog.correlation_id == correlation_id)
    ).scalars()
    if any(plain_code in str(row.change_summary) for row in rows):
        raise AssertionError("enterprise activation plaintext leaked into audit log")
