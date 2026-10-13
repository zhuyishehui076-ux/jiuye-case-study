"""T7 X2 import center API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.api.upload_guard import read_validated_xlsx, require_user_file_uploads
from app.core.audit import record_db
from app.core.config import Profile, settings
from app.core.rbac import Role
from app.db.session import get_db
from app.models import AppSetting, Student
from app.schemas.imports import (
    DemoScenarioActivateResponse,
    DemoScenarioClearRequest,
    DemoScenarioClearResponse,
    DemoScenarioResetRequest,
    DemoScenarioResponse,
    ImportBatchHistoryPage,
    ImportBatchResponse,
    ImportConfirmResponse,
    ImportEvidencePage,
    ImportIssuePage,
    ImportRollbackResponse,
    ImportRunConfirmRequest,
    ImportRunCorrectionRequest,
    ImportRunCreateRequest,
    ImportRunListItem,
    ImportRunResponse,
    ImportRunRollbackRequest,
    ImportTemplateResponse,
)
from app.seed.demo import (
    COLLEGES,
    DEMO_AS_OF_DATE,
    DEMO_PREFLIGHT_STATE_KEY,
    GRAD_YEAR,
    N_ACTIVITIES,
    N_JOBS,
    N_STUDENTS,
    SeedExists,
    SeedSourceMismatch,
    clear_demo_seed,
    has_demo_dataset_marker,
    has_unknown_business_data,
    run_demo_seed,
)
from app.services.downloads import chinese_attachment
from app.services.import_runs import (
    T16_MANAGEMENT_SOURCE,
    ImportConflictError,
    build_import_run_receipt,
    build_t16_template_xlsx,
    confirm_import_run,
    correct_run_row,
    create_import_run,
    get_import_run,
    import_run_payload,
    list_import_run_batch_history,
    list_import_run_evidence,
    list_import_run_issues,
    list_import_runs,
    list_t16_templates,
    preflight_import_run,
    rollback_import_run,
    upload_run_batch,
)
from app.services.imports import (
    LegacyImportConflictError,
    build_receipt_xlsx,
    build_template_xlsx,
    confirm_import_batch,
    ensure_legacy_upload_allowed,
    list_templates,
    rollback_import_batch,
    upload_import_batch,
    validate_demo_preflight,
)
from app.services.operation_receipts import OperationReceiptConflict
from app.services.write_idempotency import InvalidWriteOperationKey

router = APIRouter(prefix="/imports", tags=["imports"])

WRITE_ROLES = (Role.ADMIN, Role.EMPLOYMENT_OFFICE)
BUSINESS_SIGN_ROLES = (Role.EMPLOYMENT_OFFICE,)
DEMO_SCENARIO_ID = "school-employment-closed-loop"
DEMO_PREFLIGHT_KEY = DEMO_PREFLIGHT_STATE_KEY


def _require_demo_profile() -> None:
    if settings.profile is not Profile.DEMO:
        raise HTTPException(status_code=404, detail="当前档位不提供内置演示场景。")


def _require_production_profile() -> None:
    if settings.profile is not Profile.PRODUCTION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="演示环境禁止创建或修改正式数据启用记录。",
        )


def _scenario(db: Session) -> DemoScenarioResponse:
    student_count = int(db.scalar(select(func.count()).select_from(Student)) or 0)
    marker_present = has_demo_dataset_marker(db)
    blocked_reason = (
        "检测到不是由当前内置场景创建的业务数据。为避免误删，系统已禁止启用、重建和清空。"
        if has_unknown_business_data(db)
        else None
    )
    active = student_count > 0 and marker_present
    ready_setting = db.execute(
        select(AppSetting).where(AppSetting.key == DEMO_PREFLIGHT_KEY)
    ).scalar_one_or_none()
    corrected = bool(ready_setting and ready_setting.value.get("mode") == "corrected")
    if active:
        preflight = {
            "source_rows": 10,
            "inserted_count": 10,
            "updated_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "errors": [],
        }
    elif blocked_reason:
        preflight = {
            "source_rows": 0,
            "inserted_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "errors": [],
        }
    else:
        preflight = validate_demo_preflight(db, corrected=corrected)
    ready = not blocked_reason and preflight["error_count"] == 0
    return DemoScenarioResponse(
        id=DEMO_SCENARIO_ID,
        title="校方就业工作完整闭环",
        description="包含全校总看板、学院辅导员任务、单学生帮扶推岗、学生反馈和企业匿名进度。",
        school_name="航图大学（虚构）",
        cohort_year=GRAD_YEAR,
        as_of_date=DEMO_AS_OF_DATE,
        counts={
            "学院": len(COLLEGES),
            "学生": N_STUDENTS,
            "岗位": N_JOBS,
            "活动": N_ACTIVITIES,
        },
        quality_checks=[
            "所有人物、企业和联系方式均为虚构",
            "学生、帮扶、岗位、反馈和去向关系可对账",
            "业务日期固定，刷新后演示口径不漂移",
            "全程不接收外部文件",
        ],
        active=active,
        preflight_status=(
            "blocked"
            if blocked_reason
            else "active"
            if active
            else "ready"
            if ready
            else "needs_correction"
        ),
        preflight_source_rows=preflight["source_rows"],
        preflight_inserted_count=preflight["inserted_count"],
        preflight_updated_count=preflight["updated_count"],
        preflight_skipped_count=preflight["skipped_count"],
        preflight_error_count=preflight["error_count"],
        preflight_issues=preflight["errors"],
        can_activate=ready and not active,
        blocked_reason=blocked_reason,
    )


@router.get(
    "/demo-scenarios",
    response_model=list[DemoScenarioResponse],
    dependencies=[Depends(require_roles(Role.ADMIN))],
)
def demo_scenarios(db: Session = Depends(get_db)) -> list[DemoScenarioResponse]:
    _require_demo_profile()
    return [_scenario(db)]


@router.post(
    "/demo-scenarios/{scenario_id}/repair-preflight",
    response_model=DemoScenarioResponse,
    dependencies=[Depends(require_roles(Role.ADMIN))],
)
def repair_demo_scenario_preflight(
    scenario_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DemoScenarioResponse:
    _require_demo_profile()
    if scenario_id != DEMO_SCENARIO_ID:
        raise HTTPException(status_code=404, detail="未找到这个内置演示场景。")
    if int(db.scalar(select(func.count()).select_from(Student)) or 0) > 0:
        raise HTTPException(status_code=409, detail="演示数据已经启用，无需再次预检。")
    setting = db.execute(
        select(AppSetting).where(AppSetting.key == DEMO_PREFLIGHT_KEY)
    ).scalar_one_or_none()
    if setting is None:
        db.add(
            AppSetting(
                key=DEMO_PREFLIGHT_KEY,
                value={"mode": "corrected"},
                description="内置模拟质量样本已按系统建议修正",
                created_by="demo-scenario",
            )
        )
    else:
        setting.value = {"mode": "corrected"}
    record_db(
        db,
        actor=user.username,
        action="demo.preflight.repair",
        object_type="demo_scenario",
        object_id=scenario_id,
        change_summary={"mode": "corrected", "source": "repository_builtin_mock"},
    )
    db.commit()
    result = _scenario(db)
    if result.preflight_error_count:
        raise HTTPException(status_code=409, detail="重新预检仍有问题，演示数据未启用。")
    return result


def _activate_demo_scenario(
    scenario_id: str,
    *,
    reset: bool,
    actor: str,
    db: Session,
) -> DemoScenarioActivateResponse:
    _require_demo_profile()
    if scenario_id != DEMO_SCENARIO_ID:
        raise HTTPException(status_code=404, detail="未找到这个内置演示场景。")
    current = _scenario(db)
    if current.blocked_reason:
        raise HTTPException(status_code=409, detail=current.blocked_reason)
    if not reset and not current.can_activate:
        if current.active:
            raise HTTPException(
                status_code=409,
                detail="演示数据已经启用。如需从头演示，请选择‘重新生成演示数据’。",
            )
        raise HTTPException(
            status_code=409,
            detail="请先完成内置质量样本的修正与重新预检。",
        )
    try:
        counts = run_demo_seed(db, reset=reset, commit=False)
    except (SeedExists, SeedSourceMismatch) as exc:
        raise HTTPException(
            status_code=409,
            detail="演示数据已经启用。如需从头演示，请选择‘重新生成演示数据’。",
        ) from exc
    record_db(
        db,
        actor=actor,
        action="demo.scenario.reset" if reset else "demo.scenario.activate",
        object_type="demo_scenario",
        object_id=scenario_id,
        change_summary={"mock_only": True, "student_count": counts.get("students", 0)},
    )
    db.commit()
    return DemoScenarioActivateResponse(
        id=scenario_id,
        status="reset" if reset else "activated",
        counts=counts,
    )


@router.post(
    "/demo-scenarios/{scenario_id}/activate",
    response_model=DemoScenarioActivateResponse,
    dependencies=[Depends(require_roles(Role.ADMIN))],
)
def activate_demo_scenario(
    scenario_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DemoScenarioActivateResponse:
    return _activate_demo_scenario(scenario_id, reset=False, actor=user.username, db=db)


@router.post(
    "/demo-scenarios/{scenario_id}/reset",
    response_model=DemoScenarioActivateResponse,
    dependencies=[Depends(require_roles(Role.ADMIN))],
)
def reset_demo_scenario(
    scenario_id: str,
    _payload: DemoScenarioResetRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DemoScenarioActivateResponse:
    return _activate_demo_scenario(scenario_id, reset=True, actor=user.username, db=db)


@router.post(
    "/demo-scenarios/{scenario_id}/clear",
    response_model=DemoScenarioClearResponse,
    dependencies=[Depends(require_roles(Role.ADMIN))],
)
def clear_demo_scenario(
    scenario_id: str,
    _payload: DemoScenarioClearRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DemoScenarioClearResponse:
    _require_demo_profile()
    if scenario_id != DEMO_SCENARIO_ID:
        raise HTTPException(status_code=404, detail="未找到这个内置演示场景。")
    try:
        cleared = clear_demo_seed(db, commit=False)
    except SeedSourceMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_db(
        db,
        actor=user.username,
        action="demo.scenario.clear",
        object_type="demo_scenario",
        object_id=scenario_id,
        change_summary={"cleared_students": cleared, "mock_only": True},
    )
    db.commit()
    return DemoScenarioClearResponse(
        id=scenario_id,
        status="cleared",
        cleared_students=cleared,
    )


@router.post(
    "/runs",
    response_model=ImportRunResponse,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def create_run(
    payload: ImportRunCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportRunResponse:
    try:
        run = create_import_run(
            db,
            cohort_year=payload.cohort_year,
            business_note=payload.business_note,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            source_channel=T16_MANAGEMENT_SOURCE,
            idempotency_key=idempotency_key,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportRunResponse(**import_run_payload(db, run))


@router.get(
    "/runs",
    response_model=list[ImportRunListItem],
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def runs(db: Session = Depends(get_db)) -> list[ImportRunListItem]:
    return [
        ImportRunListItem(
            id=run.id,
            status=run.status,
            cohort_year=run.cohort_year,
            business_note=run.business_note,
            template_version=run.template_version,
            revision=run.revision,
            created_by=run.created_by,
            created_at=run.created_at,
            confirmed_by=run.confirmed_by,
            confirmed_at=run.confirmed_at,
            rolled_back_by=run.rolled_back_by,
            rolled_back_at=run.rolled_back_at,
            rollback_reason=run.rollback_reason,
        )
        for run in list_import_runs(db)
    ]


@router.get(
    "/runs/{run_id}",
    response_model=ImportRunResponse,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def run_detail(run_id: int, db: Session = Depends(get_db)) -> ImportRunResponse:
    try:
        run = get_import_run(db, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ImportRunResponse(**import_run_payload(db, run))


@router.get(
    "/runs/{run_id}/issues",
    response_model=ImportIssuePage,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def run_issues(
    run_id: int,
    kind: str | None = Query(default=None),
    code: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ImportIssuePage:
    try:
        get_import_run(db, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        payload = list_import_run_issues(
            db,
            run_id,
            kind=kind,
            code=code,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportIssuePage(**payload)


@router.get(
    "/runs/{run_id}/evidence",
    response_model=ImportEvidencePage,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def run_evidence(
    run_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ImportEvidencePage:
    try:
        payload = list_import_run_evidence(
            db,
            run_id,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ImportEvidencePage(**payload)


@router.get(
    "/runs/{run_id}/history",
    response_model=ImportBatchHistoryPage,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def run_batch_history(
    run_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ImportBatchHistoryPage:
    try:
        payload = list_import_run_batch_history(
            db,
            run_id,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ImportBatchHistoryPage(**payload)


@router.post(
    "/runs/{run_id}/{kind}/upload",
    response_model=ImportRunResponse,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
        Depends(require_user_file_uploads),
    ],
)
async def upload_run_xlsx(
    run_id: int,
    kind: str,
    file: UploadFile = File(...),
    revision: int = Form(..., ge=1),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportRunResponse:
    try:
        run = upload_run_batch(
            db,
            run_id=run_id,
            kind=kind,
            data=await read_validated_xlsx(file),
            filename=file.filename,
            revision=revision,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            source_channel=T16_MANAGEMENT_SOURCE,
        )
    except ImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportRunResponse(**import_run_payload(db, run))


@router.patch(
    "/runs/{run_id}/batches/{batch_id}/rows/{row_number}",
    response_model=ImportRunResponse,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def correct_run_batch_row(
    run_id: int,
    batch_id: int,
    row_number: int,
    payload: ImportRunCorrectionRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportRunResponse:
    try:
        run = correct_run_row(
            db,
            run_id=run_id,
            batch_id=batch_id,
            row_number=row_number,
            column=payload.column,
            value=payload.value,
            reason=payload.reason,
            revision=payload.revision,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            source_channel=T16_MANAGEMENT_SOURCE,
        )
    except ImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportRunResponse(**import_run_payload(db, run))


@router.post(
    "/runs/{run_id}/preflight",
    response_model=ImportRunResponse,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def preflight_run(
    run_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportRunResponse:
    try:
        run = preflight_import_run(
            db,
            run_id,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            source_channel=T16_MANAGEMENT_SOURCE,
            trigger="manual",
            commit=True,
            bump_revision=True,
            deduplicate_unchanged=True,
        )
    except ImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportRunResponse(**import_run_payload(db, run))


@router.post(
    "/runs/{run_id}/confirm",
    response_model=ImportRunResponse,
    dependencies=[
        Depends(require_roles(*BUSINESS_SIGN_ROLES)),
        Depends(_require_production_profile),
    ],
)
def confirm_run(
    run_id: int,
    payload: ImportRunConfirmRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportRunResponse:
    try:
        run = confirm_import_run(
            db,
            run_id=run_id,
            revision=payload.revision,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            source_channel=T16_MANAGEMENT_SOURCE,
        )
    except ImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportRunResponse(**import_run_payload(db, run))


@router.post(
    "/runs/{run_id}/rollback",
    response_model=ImportRunResponse,
    dependencies=[
        Depends(require_roles(*BUSINESS_SIGN_ROLES)),
        Depends(_require_production_profile),
    ],
)
def rollback_run(
    run_id: int,
    payload: ImportRunRollbackRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportRunResponse:
    try:
        run = rollback_import_run(
            db,
            run_id=run_id,
            revision=payload.revision,
            reason=payload.reason,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            source_channel=T16_MANAGEMENT_SOURCE,
        )
    except ImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportRunResponse(**import_run_payload(db, run))


@router.get(
    "/runs/{run_id}/receipt.xlsx",
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(_require_production_profile),
    ],
)
def run_receipt_xlsx(
    run_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        # Serialize against upload/correct/confirm/rollback.  The export audit,
        # workbook snapshot and commit now share this transaction.
        run = get_import_run(db, run_id, lock=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        record_db(
            db,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            source_channel=T16_MANAGEMENT_SOURCE,
            correlation_id=f"QH-{run.cohort_year}-{run.id:04d}",
            action="import.run.receipt_export",
            object_type="import_run",
            object_id=run.id,
            change_summary={
                "reason": "导出当前启用会话的校验、对账与证据回执",
                "before_status": run.status,
                "after_status": run.status,
                "before_revision": run.revision,
                "after_revision": run.revision,
            },
        )
        db.flush()
        data = build_import_run_receipt(db, run)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": chinese_attachment(
                f"{run.cohort_year}届第{run.id}次数据启用回执.xlsx",
                fallback=f"import-run-{run.id}.xlsx",
            )
        },
    )


@router.get(
    "/templates",
    response_model=list[ImportTemplateResponse],
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def templates() -> list[ImportTemplateResponse]:
    t16_rows = list_t16_templates()
    legacy_rows = [
        row for row in list_templates() if row["kind"] not in {item["kind"] for item in t16_rows}
    ]
    return [ImportTemplateResponse(**row) for row in [*t16_rows, *legacy_rows]]


@router.get(
    "/templates/{kind}.xlsx",
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def template_xlsx(
    kind: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        t16_templates = {item["kind"]: item for item in list_t16_templates()}
        if kind in t16_templates:
            data = build_t16_template_xlsx(kind)
            filename = str(t16_templates[kind]["filename"])
        else:
            data = build_template_xlsx(kind)
            filename = next(item["filename"] for item in list_templates() if item["kind"] == kind)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_db(
        db,
        actor=user.username,
        actor_id=user.user_id,
        actor_role=user.role.value,
        source_channel=T16_MANAGEMENT_SOURCE,
        action="import.template.export",
        object_type="import_template",
        object_id=kind,
        change_summary={
            "reason": "下载标准数据导入模板",
            "kind": kind,
            "template_version": t16_templates.get(kind, {}).get("schema_version"),
        },
    )
    db.commit()
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": chinese_attachment(
                filename,
                fallback=f"{kind}.xlsx",
            )
        },
    )


@router.post(
    "/{kind}/upload",
    response_model=ImportBatchResponse,
    dependencies=[
        Depends(require_roles(*WRITE_ROLES)),
        Depends(require_user_file_uploads),
    ],
)
async def upload_xlsx(
    kind: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportBatchResponse:
    try:
        # Reject superseded core-data entry points before reading the workbook.
        # The service repeats this check so non-HTTP callers cannot bypass it.
        ensure_legacy_upload_allowed(kind)
        batch = upload_import_batch(
            db,
            kind=kind,
            data=await read_validated_xlsx(file),
            filename=file.filename,
            actor=user.username,
        )
    except LegacyImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _batch_response(batch)


@router.get(
    "/batches/{batch_id}/receipt.xlsx",
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def receipt_xlsx(
    batch_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    from app.models import ImportBatch

    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    try:
        if batch.run_id is not None:
            run = get_import_run(db, batch.run_id, lock=True)
            db.refresh(batch)
            data = build_receipt_xlsx(batch)
            record_db(
                db,
                actor=user.username,
                actor_id=user.user_id,
                actor_role=user.role.value,
                source_channel=T16_MANAGEMENT_SOURCE,
                correlation_id=f"QH-{run.cohort_year}-{run.id:04d}",
                action="import.run.batch_receipt_export",
                object_type="import_run",
                object_id=run.id,
                change_summary={
                    "reason": "导出本次启用会话中的单类资料检查结果",
                    "kind": batch.target_kind,
                    "batch_ids": [batch.id],
                    "before_status": run.status,
                    "after_status": run.status,
                    "before_revision": run.revision,
                    "after_revision": run.revision,
                },
            )
        else:
            batch = db.execute(
                select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
            ).scalar_one()
            data = build_receipt_xlsx(batch)
            record_db(
                db,
                actor=user.username,
                actor_id=user.user_id,
                actor_role=user.role.value,
                source_channel=T16_MANAGEMENT_SOURCE,
                action="import.batch.receipt_export",
                object_type="import_batch",
                object_id=batch.id,
                change_summary={
                    "reason": "导出单批次导入检查结果",
                    "kind": batch.target_kind,
                    "batch_id": batch.id,
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": chinese_attachment(
                f"第{batch_id}次导入检查结果.xlsx",
                fallback=f"import-{batch_id}.xlsx",
            )
        },
    )


@router.post(
    "/batches/{batch_id}/confirm",
    response_model=ImportConfirmResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def confirm_batch(
    batch_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportConfirmResponse:
    try:
        batch, count = confirm_import_batch(db, batch_id, actor=user.username)
    except LegacyImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportConfirmResponse(batch_id=batch.id, status=batch.status, inserted_count=count)


@router.post(
    "/batches/{batch_id}/rollback",
    response_model=ImportRollbackResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def rollback_batch(
    batch_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportRollbackResponse:
    try:
        batch, count = rollback_import_batch(db, batch_id, actor=user.username)
    except LegacyImportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportRollbackResponse(batch_id=batch.id, status=batch.status, rolled_back_count=count)


def _batch_response(batch) -> ImportBatchResponse:
    return ImportBatchResponse(
        batch_id=batch.id,
        target_kind=batch.target_kind,
        status=batch.status,
        valid=batch.error_count == 0,
        row_count=batch.row_count,
        error_count=batch.error_count,
        errors=batch.errors,
    )
