"""M1 预警驾驶舱 API（SPEC §6.1）。全部只读物化视图；四角色皆可见，无任何写入口。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, require_roles
from app.core.audit import record_db
from app.core.rbac import Role
from app.db.session import get_db
from app.models import AppSetting
from app.models.setting import EMPLOYMENT_TARGET_KEY
from app.services import dashboard as svc
from app.services.operation_receipts import (
    OperationReceiptConflict,
    claim_operation,
    finish_operation,
    get_operation_result,
)
from app.services.thresholds import load_employment_target
from app.services.write_idempotency import InvalidWriteOperationKey

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

TARGET_UPDATE_OPERATION_NAMESPACE = "dashboard.target.update"

_summary_reader = require_roles(Role.ADMIN, Role.EMPLOYMENT_OFFICE, Role.LEADER_READONLY)
_school_reader = require_roles(Role.EMPLOYMENT_OFFICE, Role.LEADER_READONLY)
_alert_reader = require_roles(Role.EMPLOYMENT_OFFICE, Role.COUNSELOR)
_target_reader = require_roles(
    Role.ADMIN,
    Role.EMPLOYMENT_OFFICE,
    Role.COUNSELOR,
    Role.LEADER_READONLY,
)


class EmploymentTargetResponse(BaseModel):
    target_rate: float | None = Field(default=None, ge=0, le=100)
    revision: int = Field(ge=0)
    note: str | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None
    configured: bool


class EmploymentTargetUpdate(BaseModel):
    target_rate: float = Field(ge=0, le=100)
    note: str = Field(min_length=2, max_length=240)
    expected_revision: int = Field(ge=0)

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        note = value.strip()
        if len(note) < 2:
            raise ValueError("目标调整说明至少需要 2 个字符")
        return note


@router.get("/summary")
def summary(db: Session = Depends(get_db), _: CurrentUser = Depends(_summary_reader)) -> dict:
    return svc.get_summary(db)


@router.get("/target", response_model=EmploymentTargetResponse)
def get_target(
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_target_reader),
) -> EmploymentTargetResponse:
    return EmploymentTargetResponse.model_validate(load_employment_target(db))


@router.put("/target", response_model=EmploymentTargetResponse)
def update_target(
    payload: EmploymentTargetUpdate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(Role.EMPLOYMENT_OFFICE)),
) -> EmploymentTargetResponse:
    try:
        receipt, replayed = claim_operation(
            db,
            namespace=TARGET_UPDATE_OPERATION_NAMESPACE,
            idempotency_key=idempotency_key,
            actor=user.username,
            payload=payload.model_dump(mode="json"),
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if replayed:
        return EmploymentTargetResponse.model_validate(receipt.result_json)

    row = db.execute(
        select(AppSetting)
        .where(AppSetting.key == EMPLOYMENT_TARGET_KEY)
        .with_for_update()
    ).scalar_one_or_none()
    before = load_employment_target(db)
    if int(before["revision"]) != payload.expected_revision:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="学校目标已由其他人员更新，请重新载入后核对。",
        )
    next_revision = payload.expected_revision + 1
    next_value = {
        "target_rate": float(payload.target_rate),
        "revision": next_revision,
        "note": payload.note,
        "updated_by": user.username,
    }
    if row is None:
        row = AppSetting(
            key=EMPLOYMENT_TARGET_KEY,
            value=next_value,
            description="学校就业去向落实率目标；由就业中心配置并留痕",
            created_by=user.username,
        )
        db.add(row)
    else:
        row.value = next_value
    record_db(
        db,
        actor=user.username,
        actor_id=user.user_id,
        actor_role=user.role.value,
        source_channel="management-web",
        correlation_id=f"TARGET-{next_revision:06d}",
        action="dashboard.target.update",
        object_type="app_setting",
        object_id=EMPLOYMENT_TARGET_KEY,
        change_summary={
            "before": {
                "target_rate": before["target_rate"],
                "revision": before["revision"],
            },
            "after": {
                "target_rate": next_value["target_rate"],
                "revision": next_revision,
            },
        },
        detail=next_value["note"],
    )
    try:
        db.flush()
        response = EmploymentTargetResponse.model_validate(load_employment_target(db))
        finish_operation(receipt, result=response.model_dump(mode="json"))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="学校目标刚刚被更新，请重新载入后重试。",
        ) from None
    return response


@router.get(
    "/target-operations/{idempotency_key}",
    response_model=EmploymentTargetResponse,
)
def get_target_operation(
    idempotency_key: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(Role.EMPLOYMENT_OFFICE)),
) -> EmploymentTargetResponse:
    try:
        result = get_operation_result(
            db,
            namespace=TARGET_UPDATE_OPERATION_NAMESPACE,
            idempotency_key=idempotency_key,
            actor=user.username,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="target operation not found")
    return EmploymentTargetResponse.model_validate(result)


@router.get("/my-college-summary")
def my_college_summary(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(Role.COUNSELOR)),
) -> dict:
    if user.college_id is None:
        raise HTTPException(status_code=403, detail="当前辅导员账号未绑定学院。")
    result = svc.get_college_summary(db, user.college_id)
    if not result:
        raise HTTPException(status_code=404, detail="未找到当前学院。")
    return result


@router.get("/colleges")
def colleges(db: Session = Depends(get_db), _: CurrentUser = Depends(_school_reader)) -> list[dict]:
    return svc.get_colleges(db)


@router.get("/colleges/{college_id}/oversight")
def college_oversight(
    college_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(
        require_roles(Role.EMPLOYMENT_OFFICE, Role.LEADER_READONLY)
    ),
) -> dict:
    result = svc.get_college_oversight(db, college_id)
    if not result:
        raise HTTPException(status_code=404, detail="未找到该学院")
    return result


@router.get("/trend")
def trend(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_school_reader),
) -> list[dict]:
    return svc.get_trend(db, date_from, date_to)


@router.get("/alerts")
def alerts(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_alert_reader),
) -> list[dict]:
    college_id = user.college_id if user.role is Role.COUNSELOR else None
    return svc.get_alerts(db, limit, college_id=college_id)
