"""T10 M7 company visit expansion API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.rbac import Role
from app.db.session import get_db
from app.schemas.visits import (
    VisitCreateRequest,
    VisitCreateResponse,
    VisitDashboardResponse,
    VisitDashboardSummary,
    VisitRow,
)
from app.services.visits import (
    VisitDashboard,
    build_visit_dashboard,
    create_visit,
    get_visit_by_operation,
)
from app.services.write_idempotency import (
    InvalidWriteOperationKey,
    WriteOperationConflict,
)

router = APIRouter(prefix="/visits", tags=["visits"])

WRITE_ROLES = (Role.EMPLOYMENT_OFFICE,)
READ_ROLES = (Role.EMPLOYMENT_OFFICE,)


def _dashboard_response(dashboard: VisitDashboard) -> VisitDashboardResponse:
    return VisitDashboardResponse(
        summary=VisitDashboardSummary(**dashboard.summary.__dict__),
        rows=[VisitRow(**row.__dict__) for row in dashboard.rows],
    )


@router.post(
    "",
    response_model=VisitCreateResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def create_visit_endpoint(
    payload: VisitCreateRequest,
    idempotency_key_header: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VisitCreateResponse:
    try:
        visit, replayed = create_visit(
            db,
            company=payload.company,
            visitors=payload.visitors,
            visited_on=payload.visited_on,
            promised_jobs=payload.promised_jobs,
            follow_status=payload.follow_status,
            note=payload.note,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            idempotency_key=idempotency_key_header,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WriteOperationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    dashboard = build_visit_dashboard(db, companies=[visit.company])
    row = next(row for row in dashboard.rows if row.id == visit.id)
    return VisitCreateResponse(**row.__dict__, replayed=replayed)


@router.get(
    "/operations/{idempotency_key}",
    response_model=VisitCreateResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def visit_operation_endpoint(
    idempotency_key: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VisitCreateResponse:
    try:
        visit = get_visit_by_operation(
            db,
            idempotency_key,
            actor=user.username,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if visit is None:
        raise HTTPException(status_code=404, detail="company visit operation not found")
    dashboard = build_visit_dashboard(db, companies=[visit.company])
    row = next(row for row in dashboard.rows if row.id == visit.id)
    return VisitCreateResponse(**row.__dict__, replayed=True)


@router.get(
    "",
    response_model=list[VisitRow],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_visits_endpoint(db: Session = Depends(get_db)) -> list[VisitRow]:
    dashboard = build_visit_dashboard(db)
    return [VisitRow(**row.__dict__) for row in dashboard.rows]


@router.get(
    "/dashboard",
    response_model=VisitDashboardResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def visit_dashboard_endpoint(db: Session = Depends(get_db)) -> VisitDashboardResponse:
    return _dashboard_response(build_visit_dashboard(db))
