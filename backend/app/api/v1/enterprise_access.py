"""T19 production enterprise-account administration API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.config import settings
from app.core.rbac import Role
from app.db.session import get_db
from app.schemas.enterprise_access import (
    EnterpriseAccountPage,
    EnterpriseAccountStateRequest,
    EnterpriseAccountStateResponse,
    EnterpriseActivationIssuedItem,
    EnterpriseActivationIssueRequest,
    EnterpriseActivationIssueResponse,
    EnterpriseOrganizationUpsertRequest,
    EnterpriseOrganizationUpsertResponse,
)
from app.services.enterprise_identity import (
    ACCOUNT_ACTIVE,
    ACCOUNT_SUSPENDED,
    EnterpriseAccountInitTarget,
    EnterpriseActivationReplayUnavailable,
    EnterpriseIdentityConflict,
    EnterpriseIdentityForbidden,
    EnterpriseIdentityNotFound,
    issue_activation_materials,
    list_enterprise_accounts,
    set_enterprise_account_state,
    upsert_enterprise_organization,
)

router = APIRouter(prefix="/enterprise-access", tags=["enterprise-access"])


def _require_production_profile() -> None:
    if not settings.enterprise_production_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="正式企业账号入口仅在生产数据档可用",
        )


def _translate(exc: RuntimeError) -> HTTPException:
    if isinstance(exc, EnterpriseIdentityNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, EnterpriseIdentityForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@router.get(
    "/accounts",
    response_model=EnterpriseAccountPage,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN, Role.EMPLOYMENT_OFFICE)),
    ],
)
def accounts(
    search: str | None = Query(default=None, max_length=64),
    account_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(not_initialized|pending_activation|active|suspended)$",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> EnterpriseAccountPage:
    return EnterpriseAccountPage(
        **list_enterprise_accounts(
            db,
            search=search,
            status=account_status,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/organizations",
    response_model=EnterpriseOrganizationUpsertResponse,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN, Role.EMPLOYMENT_OFFICE)),
    ],
)
def upsert_organization(
    payload: EnterpriseOrganizationUpsertRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnterpriseOrganizationUpsertResponse:
    try:
        result = upsert_enterprise_organization(
            db,
            legal_name=payload.legal_name,
            credit_code=payload.credit_code,
            username=payload.username,
            short_name=payload.short_name,
            industry=payload.industry,
            scale=payload.scale,
            city=payload.city,
            address=payload.address,
            website=payload.website,
            summary=payload.summary,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            idempotency_key=payload.idempotency_key,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
        )
    except (EnterpriseIdentityConflict, EnterpriseIdentityNotFound) as exc:
        raise _translate(exc) from exc
    return EnterpriseOrganizationUpsertResponse(**result)


@router.post(
    "/activations",
    response_model=EnterpriseActivationIssueResponse,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN)),
    ],
)
def issue_activations(
    payload: EnterpriseActivationIssueRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnterpriseActivationIssueResponse:
    try:
        correlation_id, rows = issue_activation_materials(
            db,
            targets=[
                EnterpriseAccountInitTarget(
                    organization_id=target.organization_id,
                    account_id=target.account_id,
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
    except (
        EnterpriseActivationReplayUnavailable,
        EnterpriseIdentityConflict,
        EnterpriseIdentityNotFound,
    ) as exc:
        raise _translate(exc) from exc
    return EnterpriseActivationIssueResponse(
        correlation_id=correlation_id,
        items=[
            EnterpriseActivationIssuedItem(
                activation_id=row.activation_id,
                organization_id=row.organization_id,
                account_id=row.account_id,
                username=row.username,
                company=row.company,
                activation_code=row.activation_code,
                expires_at=row.expires_at,
            )
            for row in rows
        ],
    )


def _set_state(
    account_id: int,
    payload: EnterpriseAccountStateRequest,
    *,
    target: str,
    user: CurrentUser,
    db: Session,
) -> EnterpriseAccountStateResponse:
    try:
        account = set_enterprise_account_state(
            db,
            account_id=account_id,
            target=target,
            expected_revision=payload.revision,
            reason=payload.reason,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
        )
    except (EnterpriseIdentityConflict, EnterpriseIdentityNotFound) as exc:
        raise _translate(exc) from exc
    public_status = "pending_activation" if account.status == "pending" else account.status
    return EnterpriseAccountStateResponse(
        account_id=account.id,
        organization_id=account.organization_id,
        status=public_status,
        revision=account.revision,
        credential_version=account.credential_version,
    )


@router.post(
    "/accounts/{account_id}/suspend",
    response_model=EnterpriseAccountStateResponse,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN)),
    ],
)
def suspend_account(
    account_id: int,
    payload: EnterpriseAccountStateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnterpriseAccountStateResponse:
    return _set_state(
        account_id,
        payload,
        target=ACCOUNT_SUSPENDED,
        user=user,
        db=db,
    )


@router.post(
    "/accounts/{account_id}/restore",
    response_model=EnterpriseAccountStateResponse,
    dependencies=[
        Depends(_require_production_profile),
        Depends(require_roles(Role.ADMIN)),
    ],
)
def restore_account(
    account_id: int,
    payload: EnterpriseAccountStateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnterpriseAccountStateResponse:
    return _set_state(
        account_id,
        payload,
        target=ACCOUNT_ACTIVE,
        user=user,
        db=db,
    )
