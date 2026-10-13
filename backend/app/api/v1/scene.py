"""T9 M6 scene statistics API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.rbac import Role
from app.d2.profile import require_d2_demo_profile
from app.db.session import get_db
from app.schemas.scene import (
    MockSeedResponse,
    SceneFairOption,
    SceneMetricPush,
    SceneMetricResponse,
    SceneReport,
)
from app.services.scene import (
    build_scene_report,
    get_scene_metric_by_operation,
    list_scene_fairs,
    push_http_metric,
    seed_mock_scene_metrics,
)
from app.services.write_idempotency import (
    InvalidWriteOperationKey,
    WriteOperationConflict,
)

router = APIRouter(prefix="/scene", tags=["scene"])

READ_ROLES = (
    Role.EMPLOYMENT_OFFICE,
    Role.LEADER_READONLY,
)
WRITE_ROLES = (Role.EMPLOYMENT_OFFICE,)


@router.get(
    "/fairs",
    response_model=list[SceneFairOption],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def fairs_endpoint(db: Session = Depends(get_db)) -> list[SceneFairOption]:
    return [
        SceneFairOption(
            id=row.id,
            name=row.name,
            fair_type=row.fair_type,
            held_at=row.held_at,
            location=row.location,
        )
        for row in list_scene_fairs(db)
    ]


@router.post(
    "/mock/seed",
    response_model=MockSeedResponse,
    dependencies=[
        Depends(require_d2_demo_profile),
        Depends(require_roles(*WRITE_ROLES)),
    ],
)
def mock_seed_endpoint(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MockSeedResponse:
    fair_ids = [row.id for row in list_scene_fairs(db)[:3]]
    seed_mock_scene_metrics(db, fair_ids=fair_ids, actor=user.username)
    db.commit()
    return MockSeedResponse(seeded_fairs=len(fair_ids), fair_ids=fair_ids)


@router.post(
    "/fairs/{fair_id}/metrics",
    response_model=SceneMetricResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def push_metric_endpoint(
    fair_id: int,
    payload: SceneMetricPush,
    idempotency_key_header: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SceneMetricResponse:
    try:
        row, replayed = push_http_metric(
            db,
            fair_id=fair_id,
            zone=payload.zone,
            ts=payload.ts,
            headcount=payload.headcount,
            dwell_avg_s=payload.dwell_avg_s,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            idempotency_key=idempotency_key_header,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WriteOperationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SceneMetricResponse(
        id=row.id,
        fair_id=row.fair_id,
        zone=row.zone,
        ts_bucket=row.ts_bucket,
        headcount=row.headcount,
        dwell_avg_s=row.dwell_avg_s,
        replayed=replayed,
    )


@router.get(
    "/metric-operations/{idempotency_key}",
    response_model=SceneMetricResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def metric_operation_endpoint(
    idempotency_key: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SceneMetricResponse:
    try:
        row = get_scene_metric_by_operation(
            db,
            idempotency_key,
            actor=user.username,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="scene metric operation not found")
    return SceneMetricResponse(
        id=row.id,
        fair_id=row.fair_id,
        zone=row.zone,
        ts_bucket=row.ts_bucket,
        headcount=row.headcount,
        dwell_avg_s=row.dwell_avg_s,
        replayed=True,
    )


@router.get(
    "/fairs/{fair_id}/report",
    response_model=SceneReport,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def report_endpoint(fair_id: int, db: Session = Depends(get_db)) -> SceneReport:
    try:
        return SceneReport(**build_scene_report(db, fair_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
