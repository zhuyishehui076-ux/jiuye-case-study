"""登录与当前用户端点（SPEC §2 认证 / §6.8 X1）。"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.audit import record
from app.core.config import settings
from app.core.rbac import Role
from app.core.security import hash_password, verify_password
from app.d2.credential_release import (
    authenticate_candidate_management,
    record_candidate_management_http_receipt,
)
from app.db.session import get_db
from app.models.org import College
from app.models.user import User
from app.schemas.auth import (
    LogoutResponse,
    MeResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    TokenResponse,
)
from app.services.auth_security import (
    AuthRateLimited,
    PasswordChangeRejected,
    change_management_password,
    clear_auth_failures,
    create_management_session,
    ensure_auth_attempt_allowed,
    record_auth_failure,
    revoke_management_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def _too_many_attempts(exc: AuthRateLimited) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=str(exc),
        headers={"Retry-After": str(exc.retry_after)},
    )


@router.post("/login", response_model=TokenResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    try:
        ensure_auth_attempt_allowed(
            db, surface="management-login", principal=form.username
        )
    except AuthRateLimited as exc:
        raise _too_many_attempts(exc) from exc
    user = db.execute(
        select(User).where(User.username == form.username)
    ).scalar_one_or_none()
    stored_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(form.password, stored_hash)
    candidate_release_id: str | None = None
    if user is not None and not password_valid and settings.show_demo_badge:
        candidate_release_id = authenticate_candidate_management(
            db, username=user.username, password=form.password
        )
        password_valid = candidate_release_id is not None
    if user is None or not user.is_active or not password_valid:
        db.rollback()
        throttle = record_auth_failure(
            db, surface="management-login", principal=form.username
        )
        db.commit()
        if throttle.locked_until is not None:
            raise _too_many_attempts(
                AuthRateLimited(settings.auth_lock_minutes * 60)
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    grant = create_management_session(
        db,
        user=user,
        credential_release_id=candidate_release_id,
    )
    clear_auth_failures(db, surface="management-login", principal=form.username)
    if candidate_release_id is not None:
        record_candidate_management_http_receipt(
            db,
            release_id=candidate_release_id,
            username=user.username,
            surface="login",
        )
    db.commit()
    record(actor=user.username, action="login", obj="session")
    return TokenResponse(access_token=grant.access_token, expires_at=grant.expires_at)


@router.post("/logout", response_model=LogoutResponse)
def logout(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LogoutResponse:
    if user.session_jti is not None:
        revoke_management_session(
            db,
            user_id=user.user_id,
            session_jti=user.session_jti,
            reason="管理用户主动退出登录",
        )
        record(
            actor=user.username,
            action="logout",
            obj="management_session",
        )
        db.commit()
    return LogoutResponse()


@router.post("/change-password", response_model=PasswordChangeResponse)
def change_password(
    payload: PasswordChangeRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PasswordChangeResponse:
    try:
        revoked = change_management_password(
            db,
            user_id=user.user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
            actor=user.username,
        )
    except PasswordChangeRejected as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return PasswordChangeResponse(sessions_revoked=revoked)


@router.get("/me", response_model=MeResponse)
def me(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    college_name = None
    if user.college_id is not None:
        college_name = db.execute(
            select(College.name).where(College.id == user.college_id)
        ).scalar_one_or_none()
    response = MeResponse(
        username=user.username,
        display_name=user.username,
        role=user.role.value,
        college_id=user.college_id,
        college_name=college_name,
        show_demo_badge=settings.show_demo_badge,
        profile=settings.profile.value,
        llm_batch_enabled=settings.llm_batch_enabled,
    )
    if user.credential_release_id is not None:
        record_candidate_management_http_receipt(
            db,
            release_id=user.credential_release_id,
            username=user.username,
            surface="me",
        )
        db.commit()
    return response


@router.get("/roles")
def list_roles() -> dict:
    """暴露四角色清单（前端登录页/演示用）。"""
    return {"roles": [r.value for r in Role]}
