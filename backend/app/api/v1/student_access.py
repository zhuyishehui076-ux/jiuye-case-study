"""T18 production student-account administration API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.config import settings
from app.core.rbac import Role
from app.db.session import get_db
from app.schemas.student_access import (
    ActivationIssuedItem,
    ActivationIssueRequest,
    ActivationIssueResponse,
    ActivationRevokeRequest,
    ActivationStateResponse,
    StudentAccountPage,
    StudentAccountStateRequest,
    StudentAccountStateResponse,
)
from app.services.student_identity import (
    ACCOUNT_ACTIVE,
    ACCOUNT_SUSPENDED,
    ActivationIssueTarget,
    ActivationReplayUnavailable,
    StudentIdentityConflict,
    StudentIdentityForbidden,
    StudentIdentityNotFound,
    activation_public_status,
    issue_activation_materials,
    list_student_accounts,
    revoke_activation_material,
    set_student_account_state,
)

router = APIRouter(prefix="/student-access", tags=["student-access"])


def _require_production_profile() -> None:
    if not settings.student_production_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="正式学生账号入口仅在生产数据档可用",
        )


def _translate(exc: RuntimeError) -> HTTPException:
    if isinstance(exc, StudentIdentityNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, StudentIdentityForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@router.get(
    "/accounts",
    response_model=StudentAccountPage,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN, Role.EMPLOYMENT_OFFICE)),
    ],
)
def accounts(
    search: str | None = Query(default=None, max_length=64),
    cohort_year: int | None = Query(default=None, ge=2000, le=2100),
    college_id: int | None = Query(default=None, gt=0),
    account_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(not_initialized|pending_activation|active|suspended)$",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> StudentAccountPage:
    return StudentAccountPage(
        **list_student_accounts(
            db,
            search=search,
            cohort_year=cohort_year,
            college_id=college_id,
            status=account_status,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/activations",
    response_model=ActivationIssueResponse,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN)),
    ],
)
def issue_activations(
    payload: ActivationIssueRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivationIssueResponse:
    try:
        correlation_id, rows = issue_activation_materials(
            db,
            targets=[
                ActivationIssueTarget(
                    student_id=target.student_id,
                    expected_revision=target.expected_revision,
                    purpose=target.purpose,
                )
                for target in payload.targets
            ],
            expires_hours=payload.expires_hours,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
        )
    except (ActivationReplayUnavailable, StudentIdentityConflict, StudentIdentityNotFound) as exc:
        raise _translate(exc) from exc
    return ActivationIssueResponse(
        correlation_id=correlation_id,
        items=[
            ActivationIssuedItem(
                activation_id=row.activation_id,
                student_id=row.student_id,
                student_no=row.student_no,
                name=row.name,
                activation_code=row.activation_code,
                expires_at=row.expires_at,
            )
            for row in rows
        ],
    )


@router.post(
    "/activations/{activation_id}/revoke",
    response_model=ActivationStateResponse,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN)),
    ],
)
def revoke_activation(
    activation_id: int,
    payload: ActivationRevokeRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivationStateResponse:
    try:
        row = revoke_activation_material(
            db,
            activation_id=activation_id,
            reason=payload.reason,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
        )
    except (StudentIdentityConflict, StudentIdentityNotFound) as exc:
        raise _translate(exc) from exc
    return ActivationStateResponse(
        activation_id=row.id,
        student_id=row.student_id,
        status=activation_public_status(row),
        expires_at=row.expires_at,
    )


def _set_state(
    student_id: int,
    payload: StudentAccountStateRequest,
    *,
    target: str,
    user: CurrentUser,
    db: Session,
) -> StudentAccountStateResponse:
    try:
        account = set_student_account_state(
            db,
            student_id=student_id,
            target=target,
            expected_revision=payload.revision,
            reason=payload.reason,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
        )
    except (StudentIdentityConflict, StudentIdentityNotFound) as exc:
        raise _translate(exc) from exc
    public_status = "pending_activation" if account.status == "pending" else account.status
    return StudentAccountStateResponse(
        student_id=student_id,
        status=public_status,
        revision=account.revision,
        credential_version=account.credential_version,
    )


@router.post(
    "/accounts/{student_id}/suspend",
    response_model=StudentAccountStateResponse,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN)),
    ],
)
def suspend_account(
    student_id: int,
    payload: StudentAccountStateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StudentAccountStateResponse:
    return _set_state(
        student_id,
        payload,
        target=ACCOUNT_SUSPENDED,
        user=user,
        db=db,
    )


@router.post(
    "/accounts/{student_id}/restore",
    response_model=StudentAccountStateResponse,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN)),
    ],
)
def restore_account(
    student_id: int,
    payload: StudentAccountStateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StudentAccountStateResponse:
    return _set_state(
        student_id,
        payload,
        target=ACCOUNT_ACTIVE,
        user=user,
        db=db,
    )
