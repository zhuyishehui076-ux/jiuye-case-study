"""Enterprise H5 authentication for demo tokens and production JWT sessions."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.d2.credential_release import candidate_enterprise_token_allowed
from app.d2.enterprise_service import enterprise_token_digest
from app.db.session import get_db
from app.models import (
    EnterpriseAccessToken,
    EnterpriseAccount,
    EnterpriseOrganization,
    EnterpriseSession,
)
from app.services.enterprise_identity import enterprise_session_jti_digest


@dataclass(frozen=True)
class CurrentEnterprise:
    access_token_id: int | None
    organization_id: int | None
    account_id: int | None
    company_name: str
    expires_at: datetime
    credential_release_id: str | None = None
    session_jti: str | None = None

    @property
    def actor(self) -> str:
        if self.organization_id is not None:
            return f"enterprise:{self.organization_id}"
        return f"enterprise-demo:{self.access_token_id}"

    @property
    def is_production(self) -> bool:
        return self.organization_id is not None


def _unauthorized(auth_scheme: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="企业访问凭证无效或已过期",
        headers={"WWW-Authenticate": auth_scheme},
    )


def _get_current_demo_enterprise(
    *,
    raw_token: str | None,
    db: Session,
) -> CurrentEnterprise:
    unauthorized = _unauthorized("EnterpriseToken")
    if raw_token is None or len(raw_token) < 24:
        raise unauthorized
    digest = enterprise_token_digest(raw_token)
    row = db.execute(
        select(EnterpriseAccessToken).where(
            EnterpriseAccessToken.token_digest == digest,
        )
    ).scalar_one_or_none()
    if row is None or not hmac.compare_digest(row.token_digest, digest):
        raise unauthorized
    candidate_release_id: str | None = None
    if not row.is_active:
        candidate_release_id = candidate_enterprise_token_allowed(
            db, access_token_id=row.id
        )
        if candidate_release_id is None:
            raise unauthorized
    if row.expires_at <= datetime.now(UTC):
        raise unauthorized
    return CurrentEnterprise(
        access_token_id=row.id,
        organization_id=None,
        account_id=None,
        company_name=row.company_name,
        expires_at=row.expires_at,
        credential_release_id=candidate_release_id,
    )


def _get_current_production_enterprise(
    *,
    authorization: str | None,
    db: Session,
) -> CurrentEnterprise:
    unauthorized = _unauthorized("Bearer")
    if authorization is None or not authorization.startswith("Bearer "):
        raise unauthorized
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise unauthorized from None
    if payload.get("scope") != "enterprise" or payload.get("role") != "enterprise":
        raise unauthorized
    organization_id = payload.get("organization_id")
    account_id = payload.get("account_id")
    username = payload.get("username")
    credential_version = payload.get("credential_version")
    session_jti = payload.get("jti")
    if (
        not isinstance(organization_id, int)
        or isinstance(organization_id, bool)
        or not isinstance(account_id, int)
        or isinstance(account_id, bool)
        or not isinstance(username, str)
        or not isinstance(credential_version, int)
        or isinstance(credential_version, bool)
        or not isinstance(session_jti, str)
    ):
        raise unauthorized
    try:
        parsed_jti = UUID(session_jti)
    except ValueError:
        raise unauthorized from None
    if parsed_jti.version != 4 or str(parsed_jti) != session_jti:
        raise unauthorized
    row = db.execute(
        select(EnterpriseAccount, EnterpriseOrganization, EnterpriseSession)
        .join(
            EnterpriseOrganization,
            EnterpriseOrganization.id == EnterpriseAccount.organization_id,
        )
        .join(EnterpriseSession, EnterpriseSession.account_id == EnterpriseAccount.id)
        .where(
            EnterpriseAccount.id == account_id,
            EnterpriseAccount.organization_id == organization_id,
            EnterpriseAccount.username == username,
            EnterpriseAccount.status == "active",
            EnterpriseAccount.credential_version == credential_version,
            EnterpriseOrganization.status == "verified",
            EnterpriseSession.organization_id == organization_id,
            EnterpriseSession.jti_digest == enterprise_session_jti_digest(session_jti),
            EnterpriseSession.revoked_at.is_(None),
            EnterpriseSession.expires_at > datetime.now(UTC),
        )
    ).one_or_none()
    if row is None:
        raise unauthorized
    account, organization, session = row
    return CurrentEnterprise(
        access_token_id=None,
        organization_id=organization.id,
        account_id=account.id,
        company_name=organization.short_name or organization.legal_name,
        expires_at=session.expires_at,
        session_jti=session_jti,
    )


def get_current_enterprise(
    raw_token: Annotated[str | None, Header(alias="X-Enterprise-Token")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Session = Depends(get_db),
) -> CurrentEnterprise:
    runtime = get_settings()
    if runtime.enterprise_production_auth_enabled:
        if raw_token is not None:
            raise HTTPException(status_code=404, detail="生产企业端不接受演示企业令牌")
        return _get_current_production_enterprise(authorization=authorization, db=db)
    if authorization is not None:
        raise HTTPException(status_code=400, detail="演示企业端不接受正式企业账号令牌")
    return _get_current_demo_enterprise(raw_token=raw_token, db=db)
