"""T18 student H5/weapp endpoints with strict Demo/Production credentials."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.core.config import get_settings
from app.core.security import create_student_access_token
from app.d2.credential_release import (
    authenticate_candidate_student,
    record_candidate_student_http_receipt,
)
from app.d2.student_auth import (
    CurrentStudent,
    get_current_student,
)
from app.d2.student_schemas import (
    ConsentState,
    ConsentUpdateRequest,
    FeedbackResponse,
    FeedbackUpdate,
    GrowthResponse,
    JobActionCreate,
    JobActionResponse,
    RecommendationResponse,
    StudentActivationRequest,
    StudentApplicationCreate,
    StudentApplicationResponse,
    StudentDisclosureUpdate,
    StudentFairCheckinRequest,
    StudentFairCheckinResponse,
    StudentFairItem,
    StudentJobDetailResponse,
    StudentLoginRequest,
    StudentLogoutResponse,
    StudentMeResponse,
    StudentProgressResponse,
    StudentTokenResponse,
)
from app.d2.student_service import (
    StudentForbiddenError,
    StudentNotFoundError,
    authenticate_binding,
    create_job_action,
    ensure_self,
    growth,
    list_job_actions,
    recommendations,
    update_match_feedback,
)
from app.db.session import get_db
from app.services.auth_security import (
    AuthRateLimited,
    clear_auth_failures,
    ensure_auth_attempt_allowed,
    record_auth_failure,
)
from app.services.fairs import (
    FairConflictError,
    checkin_student_self,
    list_student_fairs,
)
from app.services.student_feedback import public_feedback
from app.services.student_identity import (
    StudentIdentityConflict,
    StudentIdentityForbidden,
    StudentIdentityNotFound,
    activate_student_account,
    login_student_account,
    logout_student_session,
)
from app.services.student_portal import (
    StudentPortalConflict,
    StudentPortalForbidden,
    StudentPortalNotFound,
    create_student_application,
    list_student_applications,
    production_recommendations,
    read_student_consent,
    student_job_detail,
    student_me,
    student_progress,
    update_application_disclosure,
    update_production_feedback,
    write_student_consent,
)

router = APIRouter(
    prefix="/student",
    tags=["student"],
)


def _student_rate_limited(exc: AuthRateLimited) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=str(exc),
        headers={"Retry-After": str(exc.retry_after)},
    )


def _record_student_auth_failure(
    db: Session, *, surface: str, principal: str
) -> None:
    db.rollback()
    row = record_auth_failure(db, surface=surface, principal=principal)
    db.commit()
    if row.locked_until is not None:
        raise _student_rate_limited(
            AuthRateLimited(get_settings().auth_lock_minutes * 60)
        )


@router.get("/fairs", response_model=list[StudentFairItem])
def student_fairs(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> list[StudentFairItem]:
    return [
        StudentFairItem(**row)
        for row in list_student_fairs(db, student_id=current.student_id)
    ]


@router.post(
    "/fairs/{fair_id}/checkin",
    response_model=StudentFairCheckinResponse,
)
def student_fair_checkin(
    fair_id: int,
    payload: StudentFairCheckinRequest,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> StudentFairCheckinResponse:
    try:
        row, already_checked_in = checkin_student_self(
            db,
            fair_id,
            student_id=current.student_id,
            gate_id=payload.gate_id,
            idempotency_key=payload.idempotency_key,
            checkin_code=payload.checkin_code,
            actor=current.actor,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StudentFairCheckinResponse(
        checkin_id=row.id,
        fair_id=row.fair_id,
        checked_in_at=row.at,
        already_checked_in=already_checked_in,
    )


def _translate_service_error(exc: RuntimeError) -> HTTPException:
    if isinstance(
        exc,
        StudentForbiddenError | StudentIdentityForbidden | StudentPortalForbidden,
    ):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, StudentIdentityConflict | StudentPortalConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/auth/login", response_model=StudentTokenResponse)
def student_login(
    payload: StudentLoginRequest,
    db: Session = Depends(get_db),
) -> StudentTokenResponse:
    runtime = get_settings()
    if runtime.student_production_auth_enabled:
        if payload.binding_code is not None or payload.wx_code is not None:
            raise HTTPException(status_code=400, detail="正式学生登录不接受演示绑定码或伪造微信码")
        if not payload.student_no or not payload.password:
            raise HTTPException(status_code=400, detail="请填写学号和密码")
        try:
            ensure_auth_attempt_allowed(
                db, surface="student-login", principal=payload.student_no
            )
        except AuthRateLimited as exc:
            raise _student_rate_limited(exc) from exc
        try:
            grant = login_student_account(
                db,
                student_no=payload.student_no,
                password=payload.password,
            )
        except StudentIdentityNotFound as exc:
            _record_student_auth_failure(
                db, surface="student-login", principal=payload.student_no
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc
        except StudentIdentityForbidden as exc:
            _record_student_auth_failure(
                db, surface="student-login", principal=payload.student_no
            )
            raise _translate_service_error(exc) from exc
        clear_auth_failures(db, surface="student-login", principal=payload.student_no)
        db.commit()
        return StudentTokenResponse(
            access_token=grant.access_token,
            student_id=grant.student.id,
            student_no=grant.student.student_no,
            name=grant.student.name,
            show_demo_badge=False,
            profile="production",
            expires_at=grant.expires_at,
        )

    if not payload.binding_code:
        raise HTTPException(status_code=400, detail="演示绑定码不能为空")
    if payload.wx_code is not None and not payload.wx_code.strip():
        raise HTTPException(status_code=400, detail="微信登录码格式无效")
    if payload.student_no is not None or payload.password is not None:
        raise HTTPException(status_code=400, detail="演示档不接受正式学生账号密码")
    candidate_release_id: str | None = None
    try:
        binding, student = authenticate_binding(db, payload.binding_code)
    except StudentNotFoundError as exc:
        candidate = authenticate_candidate_student(
            db, binding_code=payload.binding_code
        )
        if candidate is None:
            raise HTTPException(status_code=401, detail="绑定码无效") from exc
        candidate_release_id, binding, student = candidate
    binding.last_login_at = datetime.now(UTC)
    record_db(
        db,
        actor=f"student:{student.id}",
        action="student.login",
        object_type="student_session",
        object_id=student.id,
    )
    access_token = create_student_access_token(
        student_id=student.id,
        student_no=student.student_no,
        credential_version=binding.credential_version,
        credential_release_id=candidate_release_id,
    )
    if candidate_release_id is not None:
        record_candidate_student_http_receipt(
            db,
            release_id=candidate_release_id,
            student_id=student.id,
            surface="login",
        )
    db.commit()
    return StudentTokenResponse(
        access_token=access_token,
        student_id=student.id,
        student_no=student.student_no,
        name=student.name,
        show_demo_badge=runtime.show_demo_badge,
        profile="demo",
    )


@router.post("/auth/activate", response_model=StudentTokenResponse)
def student_activate(
    payload: StudentActivationRequest,
    db: Session = Depends(get_db),
) -> StudentTokenResponse:
    runtime = get_settings()
    if not runtime.student_production_auth_enabled:
        raise HTTPException(status_code=404, detail="正式学生激活入口仅在生产数据档可用")
    try:
        ensure_auth_attempt_allowed(
            db, surface="student-activate", principal=payload.student_no
        )
    except AuthRateLimited as exc:
        raise _student_rate_limited(exc) from exc
    try:
        grant = activate_student_account(
            db,
            student_no=payload.student_no,
            activation_code=payload.activation_code,
            password=payload.password,
            consents=payload.consents.model_dump(),
        )
    except (
        StudentIdentityNotFound,
        StudentIdentityForbidden,
        StudentIdentityConflict,
    ) as exc:
        _record_student_auth_failure(
            db, surface="student-activate", principal=payload.student_no
        )
        raise _translate_service_error(exc) from exc
    clear_auth_failures(db, surface="student-activate", principal=payload.student_no)
    db.commit()
    return StudentTokenResponse(
        access_token=grant.access_token,
        student_id=grant.student.id,
        student_no=grant.student.student_no,
        name=grant.student.name,
        show_demo_badge=False,
        profile="production",
        expires_at=grant.expires_at,
    )


@router.post("/auth/logout", response_model=StudentLogoutResponse)
def student_logout(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> StudentLogoutResponse:
    if get_settings().student_production_auth_enabled:
        if current.session_jti is None:
            raise HTTPException(status_code=401, detail="当前学生会话无效")
        try:
            logout_student_session(
                db,
                student_id=current.student_id,
                session_jti=current.session_jti,
            )
        except StudentIdentityNotFound as exc:
            raise _translate_service_error(exc) from exc
    return StudentLogoutResponse()


@router.get("/me", response_model=StudentMeResponse)
def get_me(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> StudentMeResponse:
    try:
        return StudentMeResponse(**student_me(db, student_id=current.student_id))
    except StudentPortalNotFound as exc:
        raise _translate_service_error(exc) from exc


@router.get("/me/recommendations", response_model=RecommendationResponse)
def get_my_recommendations(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    return RecommendationResponse(
        **production_recommendations(db, student_id=current.student_id)
    )


@router.get("/me/jobs/{job_id}", response_model=StudentJobDetailResponse)
def get_my_job(
    job_id: int,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> StudentJobDetailResponse:
    try:
        return StudentJobDetailResponse(
            **student_job_detail(db, student_id=current.student_id, job_id=job_id)
        )
    except (StudentPortalNotFound, StudentPortalForbidden) as exc:
        raise _translate_service_error(exc) from exc


@router.put(
    "/me/matches/{match_id}/feedback",
    response_model=FeedbackResponse,
)
def put_my_feedback(
    match_id: int,
    payload: FeedbackUpdate,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> FeedbackResponse:
    try:
        match = update_production_feedback(
            db,
            student_id=current.student_id,
            match_id=match_id,
            feedback=payload.feedback,
        )
    except (
        StudentPortalNotFound,
        StudentPortalForbidden,
        StudentPortalConflict,
    ) as exc:
        raise _translate_service_error(exc) from exc
    label = public_feedback(match.feedback)
    if label is None:
        raise HTTPException(status_code=500, detail="推荐反馈归一化失败")
    return FeedbackResponse(match_id=match.id, feedback=label)


@router.post("/me/applications", response_model=StudentApplicationResponse)
def post_my_application(
    payload: StudentApplicationCreate,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> StudentApplicationResponse:
    try:
        result = create_student_application(
            db,
            student_id=current.student_id,
            job_id=payload.job_id,
            match_id=payload.match_id,
            disclose_to_employer=payload.disclose_to_employer,
            idempotency_key=payload.idempotency_key,
            fair_id=payload.fair_id,
        )
    except (
        StudentPortalNotFound,
        StudentPortalForbidden,
        StudentPortalConflict,
    ) as exc:
        raise _translate_service_error(exc) from exc
    return StudentApplicationResponse(**result)


@router.get("/me/applications", response_model=list[StudentApplicationResponse])
def get_my_applications(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> list[StudentApplicationResponse]:
    return [
        StudentApplicationResponse(**row)
        for row in list_student_applications(db, student_id=current.student_id)
    ]


@router.put(
    "/me/applications/{application_id}/disclosure",
    response_model=StudentApplicationResponse,
)
def put_my_application_disclosure(
    application_id: int,
    payload: StudentDisclosureUpdate,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> StudentApplicationResponse:
    try:
        result = update_application_disclosure(
            db,
            student_id=current.student_id,
            application_id=application_id,
            authorized=payload.authorized,
            revision=payload.revision,
            idempotency_key=payload.idempotency_key,
        )
    except (StudentPortalNotFound, StudentPortalConflict) as exc:
        raise _translate_service_error(exc) from exc
    return StudentApplicationResponse(**result)


@router.get("/me/fairs", response_model=list[StudentFairItem])
def get_my_fairs(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> list[StudentFairItem]:
    return [
        StudentFairItem(**row)
        for row in list_student_fairs(db, student_id=current.student_id)
    ]


@router.post(
    "/me/fairs/{fair_id}/checkin",
    response_model=StudentFairCheckinResponse,
)
def post_my_fair_checkin(
    fair_id: int,
    payload: StudentFairCheckinRequest,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> StudentFairCheckinResponse:
    try:
        row, already_checked_in = checkin_student_self(
            db,
            fair_id,
            student_id=current.student_id,
            gate_id=payload.gate_id,
            idempotency_key=payload.idempotency_key,
            checkin_code=payload.checkin_code,
            actor=current.actor,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StudentFairCheckinResponse(
        checkin_id=row.id,
        fair_id=row.fair_id,
        checked_in_at=row.at,
        already_checked_in=already_checked_in,
    )


@router.get("/me/progress", response_model=StudentProgressResponse)
def get_my_progress(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> StudentProgressResponse:
    try:
        return StudentProgressResponse(
            **student_progress(db, student_id=current.student_id)
        )
    except StudentPortalNotFound as exc:
        raise _translate_service_error(exc) from exc


@router.get("/me/consents", response_model=ConsentState)
def get_my_consents(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> ConsentState:
    return ConsentState(**read_student_consent(db, student_id=current.student_id))


@router.put("/me/consents", response_model=ConsentState)
def put_my_consents(
    payload: ConsentUpdateRequest,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> ConsentState:
    try:
        result = write_student_consent(
            db,
            student_id=current.student_id,
            values=payload.model_dump(exclude={"expected_revision"}),
            expected_revision=payload.expected_revision,
        )
    except (StudentPortalConflict, StudentPortalNotFound) as exc:
        raise _translate_service_error(exc) from exc
    return ConsentState(**result)


@router.get("/me/growth", response_model=GrowthResponse)
def get_my_growth(
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> GrowthResponse:
    try:
        result = growth(
            db,
            requested_student_id=current.student_id,
            token_student_id=current.student_id,
        )
    except (StudentForbiddenError, StudentNotFoundError) as exc:
        raise _translate_service_error(exc) from exc
    return GrowthResponse(**result)


@router.get(
    "/students/{student_id}/recommendations",
    response_model=RecommendationResponse,
)
def list_recommendations(
    student_id: int,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    if get_settings().student_production_auth_enabled:
        try:
            ensure_self(
                requested_student_id=student_id,
                token_student_id=current.student_id,
            )
        except StudentForbiddenError as exc:
            raise _translate_service_error(exc) from exc
        return RecommendationResponse(
            **production_recommendations(db, student_id=current.student_id)
        )
    try:
        result = recommendations(
            db,
            requested_student_id=student_id,
            token_student_id=current.student_id,
        )
    except (StudentForbiddenError, StudentNotFoundError) as exc:
        raise _translate_service_error(exc) from exc
    return RecommendationResponse(**result)


@router.put(
    "/students/{student_id}/matches/{match_id}/feedback",
    response_model=FeedbackResponse,
)
def set_match_feedback(
    student_id: int,
    match_id: int,
    payload: FeedbackUpdate,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> FeedbackResponse:
    if get_settings().student_production_auth_enabled:
        try:
            ensure_self(
                requested_student_id=student_id,
                token_student_id=current.student_id,
            )
            match = update_production_feedback(
                db,
                student_id=current.student_id,
                match_id=match_id,
                feedback=payload.feedback,
            )
        except (
            StudentForbiddenError,
            StudentPortalNotFound,
            StudentPortalConflict,
        ) as exc:
            raise _translate_service_error(exc) from exc
        label = public_feedback(match.feedback)
        if label is None:
            raise HTTPException(status_code=500, detail="推荐反馈归一化失败")
        return FeedbackResponse(match_id=match.id, feedback=label)
    try:
        match = update_match_feedback(
            db,
            requested_student_id=student_id,
            token_student_id=current.student_id,
            match_id=match_id,
            feedback=payload.feedback,
        )
    except (StudentForbiddenError, StudentNotFoundError) as exc:
        raise _translate_service_error(exc) from exc
    label = public_feedback(match.feedback)
    if label is None:
        raise HTTPException(status_code=500, detail="feedback normalization failed")
    return FeedbackResponse(match_id=match.id, feedback=label)


@router.get("/students/{student_id}/growth", response_model=GrowthResponse)
def get_growth(
    student_id: int,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> GrowthResponse:
    try:
        result = growth(
            db,
            requested_student_id=student_id,
            token_student_id=current.student_id,
        )
    except (StudentForbiddenError, StudentNotFoundError) as exc:
        raise _translate_service_error(exc) from exc
    response = GrowthResponse(**result)
    if current.credential_release_id is not None:
        record_candidate_student_http_receipt(
            db,
            release_id=current.credential_release_id,
            student_id=current.student_id,
            surface="growth",
        )
        db.commit()
    return response


@router.get("/students/{student_id}/consents", response_model=ConsentState)
def get_consents(
    student_id: int,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> ConsentState:
    try:
        ensure_self(
            requested_student_id=student_id,
            token_student_id=current.student_id,
        )
    except StudentForbiddenError as exc:
        raise _translate_service_error(exc) from exc
    return ConsentState(
        **read_student_consent(db, student_id=current.student_id)
    )


@router.put("/students/{student_id}/consents", response_model=ConsentState)
def put_consents(
    student_id: int,
    payload: ConsentUpdateRequest,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> ConsentState:
    try:
        ensure_self(
            requested_student_id=student_id,
            token_student_id=current.student_id,
        )
        result = write_student_consent(
            db,
            student_id=current.student_id,
            values=payload.model_dump(exclude={"expected_revision"}),
            expected_revision=payload.expected_revision,
        )
    except (StudentForbiddenError, StudentPortalConflict, StudentPortalNotFound) as exc:
        raise _translate_service_error(exc) from exc
    return ConsentState(**result)


@router.post("/students/{student_id}/job-actions", response_model=JobActionResponse)
def post_job_action(
    student_id: int,
    payload: JobActionCreate,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> JobActionResponse:
    if get_settings().student_production_auth_enabled:
        raise HTTPException(
            status_code=404,
            detail="正式学生投递请使用可追溯的岗位申请入口",
        )
    try:
        row = create_job_action(
            db,
            requested_student_id=student_id,
            token_student_id=current.student_id,
            company=payload.company,
            job_title=payload.job_title,
            occurred_at=payload.occurred_at,
        )
    except StudentForbiddenError as exc:
        raise _translate_service_error(exc) from exc
    return JobActionResponse(
        action_id=row.id,
        action_type="投递",
        target=row.target or "",
        occurred_at=row.occurred_at,
    )


@router.get("/students/{student_id}/job-actions", response_model=list[JobActionResponse])
def get_job_actions(
    student_id: int,
    current: CurrentStudent = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> list[JobActionResponse]:
    if get_settings().student_production_auth_enabled:
        raise HTTPException(
            status_code=404,
            detail="正式学生进展请使用本人就业航线入口",
        )
    try:
        rows = list_job_actions(
            db,
            requested_student_id=student_id,
            token_student_id=current.student_id,
        )
    except StudentForbiddenError as exc:
        raise _translate_service_error(exc) from exc
    return [
        JobActionResponse(
            action_id=row.id,
            action_type="投递",
            target=row.target or "",
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]
