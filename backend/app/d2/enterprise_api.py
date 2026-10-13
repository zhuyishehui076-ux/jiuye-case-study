"""Enterprise H5 endpoints with demo/production credential isolation."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.d2.credential_release import record_candidate_enterprise_http_receipt
from app.d2.enterprise_auth import CurrentEnterprise, get_current_enterprise
from app.d2.enterprise_schemas import (
    EnterpriseActivateRequest,
    EnterpriseApplicationTransitionRequest,
    EnterpriseApplicationTransitionResponse,
    EnterpriseCandidatePage,
    EnterpriseFairCheckinRequest,
    EnterpriseFairCheckinResponse,
    EnterpriseFairItem,
    EnterpriseFairRegistrationRequest,
    EnterpriseJobSubmitRequest,
    EnterpriseLoginRequest,
    EnterpriseLogoutResponse,
    EnterpriseProfileResponse,
    EnterpriseProfileUpdateRequest,
    EnterpriseSessionResponse,
    EnterpriseSubmissionResponse,
    EnterpriseSupplyReportResponse,
    EnterpriseTokenResponse,
)
from app.d2.enterprise_service import (
    EnterpriseRateLimitError,
    create_enterprise_submission,
    enterprise_production_session_payload,
    enterprise_profile,
    enterprise_session_payload,
    enterprise_supply_report,
    list_enterprise_candidates,
    list_enterprise_submissions,
    process_enterprise_submission,
    transition_enterprise_application,
    update_enterprise_profile,
)
from app.db.session import get_db
from app.services.auth_security import (
    AuthRateLimited,
    clear_auth_failures,
    ensure_auth_attempt_allowed,
    record_auth_failure,
)
from app.services.enterprise_identity import (
    EnterpriseIdentityConflict,
    EnterpriseIdentityForbidden,
    EnterpriseIdentityNotFound,
    activate_enterprise_account,
    login_enterprise_account,
    logout_enterprise_session,
)
from app.services.fairs import (
    FairConflictError,
    checkin_enterprise,
    list_enterprise_fairs,
    register_company,
)

router = APIRouter(prefix="/enterprise", tags=["enterprise"])


def _enterprise_rate_limited(exc: AuthRateLimited) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=str(exc),
        headers={"Retry-After": str(exc.retry_after)},
    )


def _record_enterprise_auth_failure(
    db: Session, *, surface: str, principal: str
) -> None:
    db.rollback()
    row = record_auth_failure(db, surface=surface, principal=principal)
    db.commit()
    if row.locked_until is not None:
        raise _enterprise_rate_limited(
            AuthRateLimited(get_settings().auth_lock_minutes * 60)
        )


def _translate_identity(exc: RuntimeError) -> HTTPException:
    if isinstance(exc, EnterpriseIdentityNotFound):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    if isinstance(exc, EnterpriseIdentityForbidden):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _require_production(current: CurrentEnterprise) -> None:
    if not current.is_production or current.organization_id is None:
        raise HTTPException(status_code=404, detail="该入口仅正式企业账号可用")


@router.post("/auth/login", response_model=EnterpriseTokenResponse)
def enterprise_login(
    payload: EnterpriseLoginRequest,
    db: Session = Depends(get_db),
) -> EnterpriseTokenResponse:
    runtime = get_settings()
    if runtime.enterprise_production_auth_enabled:
        if payload.enterprise_token is not None:
            raise HTTPException(status_code=400, detail="生产企业端不接受演示企业令牌")
        if not payload.username or not payload.password:
            raise HTTPException(status_code=400, detail="请填写企业账号和密码")
        try:
            ensure_auth_attempt_allowed(
                db, surface="enterprise-login", principal=payload.username
            )
        except AuthRateLimited as exc:
            raise _enterprise_rate_limited(exc) from exc
        try:
            grant = login_enterprise_account(
                db,
                username=payload.username,
                password=payload.password,
            )
        except (
            EnterpriseIdentityNotFound,
            EnterpriseIdentityForbidden,
            EnterpriseIdentityConflict,
        ) as exc:
            _record_enterprise_auth_failure(
                db, surface="enterprise-login", principal=payload.username
            )
            raise _translate_identity(exc) from exc
        clear_auth_failures(db, surface="enterprise-login", principal=payload.username)
        db.commit()
        return EnterpriseTokenResponse(
            access_token=grant.access_token,
            organization_id=grant.organization.id,
            account_id=grant.account.id,
            company=grant.organization.short_name or grant.organization.legal_name,
            username=grant.account.username,
            show_demo_badge=False,
            profile="production",
            expires_at=grant.expires_at,
        )
    raise HTTPException(status_code=404, detail="演示企业端继续使用演示令牌请求业务接口")


@router.post("/auth/activate", response_model=EnterpriseTokenResponse)
def enterprise_activate(
    payload: EnterpriseActivateRequest,
    db: Session = Depends(get_db),
) -> EnterpriseTokenResponse:
    if not get_settings().enterprise_production_auth_enabled:
        raise HTTPException(status_code=404, detail="正式企业激活入口仅在生产数据档可用")
    try:
        ensure_auth_attempt_allowed(
            db, surface="enterprise-activate", principal=payload.username
        )
    except AuthRateLimited as exc:
        raise _enterprise_rate_limited(exc) from exc
    try:
        grant = activate_enterprise_account(
            db,
            username=payload.username,
            activation_code=payload.activation_code,
            password=payload.password,
        )
    except (
        EnterpriseIdentityNotFound,
        EnterpriseIdentityForbidden,
        EnterpriseIdentityConflict,
    ) as exc:
        _record_enterprise_auth_failure(
            db, surface="enterprise-activate", principal=payload.username
        )
        raise _translate_identity(exc) from exc
    clear_auth_failures(db, surface="enterprise-activate", principal=payload.username)
    db.commit()
    return EnterpriseTokenResponse(
        access_token=grant.access_token,
        organization_id=grant.organization.id,
        account_id=grant.account.id,
        company=grant.organization.short_name or grant.organization.legal_name,
        username=grant.account.username,
        show_demo_badge=False,
        profile="production",
        expires_at=grant.expires_at,
    )


@router.post("/auth/logout", response_model=EnterpriseLogoutResponse)
def enterprise_logout(
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseLogoutResponse:
    if current.is_production:
        if current.organization_id is None or current.session_jti is None:
            raise HTTPException(status_code=401, detail="当前企业会话无效")
        try:
            logout_enterprise_session(
                db,
                organization_id=current.organization_id,
                session_jti=current.session_jti,
            )
        except EnterpriseIdentityNotFound as exc:
            raise _translate_identity(exc) from exc
    return EnterpriseLogoutResponse()


@router.get("/session", response_model=EnterpriseSessionResponse)
def enterprise_session(
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseSessionResponse:
    if current.is_production:
        assert current.organization_id is not None and current.account_id is not None
        return EnterpriseSessionResponse(
            **enterprise_production_session_payload(
                db,
                organization_id=current.organization_id,
                account_id=current.account_id,
                expires_at=current.expires_at,
            )
        )
    assert current.access_token_id is not None
    response = EnterpriseSessionResponse(
        **enterprise_session_payload(db, access_token_id=current.access_token_id)
    )
    if current.credential_release_id is not None:
        record_candidate_enterprise_http_receipt(
            db,
            release_id=current.credential_release_id,
            access_token_id=current.access_token_id,
        )
        db.commit()
    return response


@router.get("/profile", response_model=EnterpriseProfileResponse)
def get_profile(
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseProfileResponse:
    _require_production(current)
    assert current.organization_id is not None
    return EnterpriseProfileResponse(
        **enterprise_profile(db, organization_id=current.organization_id)
    )


@router.put("/profile", response_model=EnterpriseProfileResponse)
def put_profile(
    payload: EnterpriseProfileUpdateRequest,
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseProfileResponse:
    _require_production(current)
    assert current.organization_id is not None
    try:
        result = update_enterprise_profile(
            db,
            organization_id=current.organization_id,
            values=payload.model_dump(
                exclude={"expected_revision", "idempotency_key"},
                exclude_unset=True,
            ),
            expected_revision=payload.expected_revision,
            idempotency_key=payload.idempotency_key,
            actor=current.actor,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EnterpriseProfileResponse(**result)


@router.post(
    "/jobs",
    response_model=EnterpriseSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_enterprise_job(
    payload: EnterpriseJobSubmitRequest,
    background_tasks: BackgroundTasks,
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseSubmissionResponse:
    try:
        submission = create_enterprise_submission(
            db,
            access_token_id=current.access_token_id,
            organization_id=current.organization_id,
            title=payload.title,
            raw_text=payload.raw_text,
            idempotency_key=payload.idempotency_key,
        )
    except EnterpriseRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="提交频率已达上限，请稍后再试",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(process_enterprise_submission, submission.id)
    rows = list_enterprise_submissions(
        db,
        access_token_id=current.access_token_id,
        organization_id=current.organization_id,
    )
    return EnterpriseSubmissionResponse(
        **next(row for row in rows if row["submission_id"] == submission.id)
    )


@router.get("/jobs", response_model=list[EnterpriseSubmissionResponse])
def enterprise_jobs(
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> list[EnterpriseSubmissionResponse]:
    return [
        EnterpriseSubmissionResponse(**row)
        for row in list_enterprise_submissions(
            db,
            access_token_id=current.access_token_id,
            organization_id=current.organization_id,
        )
    ]


@router.get("/supply-report", response_model=EnterpriseSupplyReportResponse)
def supply_report(
    _current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseSupplyReportResponse:
    return EnterpriseSupplyReportResponse(**enterprise_supply_report(db))


@router.get("/fairs", response_model=list[EnterpriseFairItem])
def enterprise_fairs(
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> list[EnterpriseFairItem]:
    return [
        EnterpriseFairItem(**row)
        for row in list_enterprise_fairs(
            db,
            access_token_id=current.access_token_id,
            organization_id=current.organization_id,
        )
    ]


@router.post("/fairs/register", response_model=EnterpriseFairItem)
def enterprise_fair_register(
    payload: EnterpriseFairRegistrationRequest,
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseFairItem:
    _require_production(current)
    assert current.organization_id is not None
    try:
        row = register_company(
            db,
            payload.fair_id,
            company=current.company_name,
            title=payload.title,
            raw_text=payload.raw_text,
            booth=None,
            actor=current.actor,
            idempotency_key=str(payload.idempotency_key),
            enterprise_organization_id=current.organization_id,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    fair = next(
        item
        for item in list_enterprise_fairs(db, organization_id=current.organization_id)
        if item["fair_company_id"] == row.id
    )
    return EnterpriseFairItem(**fair)


@router.post(
    "/fairs/{fair_id}/checkin",
    response_model=EnterpriseFairCheckinResponse,
)
def enterprise_fair_checkin(
    fair_id: int,
    payload: EnterpriseFairCheckinRequest,
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseFairCheckinResponse:
    try:
        row, already_checked_in = checkin_enterprise(
            db,
            fair_id,
            access_token_id=current.access_token_id,
            organization_id=current.organization_id,
            representative_name=payload.representative_name,
            gate_id=payload.gate_id,
            idempotency_key=payload.idempotency_key,
            actor=current.actor,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EnterpriseFairCheckinResponse(
        checkin_id=row.id,
        fair_id=fair_id,
        checked_in_at=row.at,
        already_checked_in=already_checked_in,
    )


@router.get("/candidates", response_model=EnterpriseCandidatePage)
def enterprise_candidates(
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> EnterpriseCandidatePage:
    _require_production(current)
    assert current.organization_id is not None
    return EnterpriseCandidatePage(
        **list_enterprise_candidates(
            db,
            organization_id=current.organization_id,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/candidates/{application_id}/transition",
    response_model=EnterpriseApplicationTransitionResponse,
)
def transition_candidate(
    application_id: int,
    payload: EnterpriseApplicationTransitionRequest,
    current: CurrentEnterprise = Depends(get_current_enterprise),
    db: Session = Depends(get_db),
) -> EnterpriseApplicationTransitionResponse:
    _require_production(current)
    assert current.organization_id is not None and current.account_id is not None
    try:
        result = transition_enterprise_application(
            db,
            organization_id=current.organization_id,
            account_id=current.account_id,
            application_id=application_id,
            target_status=payload.target_status,
            reason=payload.reason,
            revision=payload.revision,
            idempotency_key=payload.idempotency_key,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EnterpriseApplicationTransitionResponse(**result)
