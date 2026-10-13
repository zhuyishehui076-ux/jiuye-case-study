"""T8 M5 recruitment fair API."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.audit import record_db
from app.core.rbac import Role
from app.db.session import get_db
from app.schemas.fairs import (
    FairCheckinCorrection,
    FairCheckinCreate,
    FairCheckinResponse,
    FairCompanyCreate,
    FairCompanyResponse,
    FairCompanyReview,
    FairCreate,
    FairFunnelResponse,
    FairResponse,
    FairSnapshotResponse,
    FairStatusUpdate,
    FairStudentCheckinCodeIssue,
    FairStudentCheckinCodeResponse,
    FairUpdate,
)
from app.services.downloads import chinese_attachment
from app.services.fairs import (
    FairConflictError,
    StudentOutsideCollegeError,
    checkin_student,
    correct_student_checkin,
    create_fair,
    delete_fair,
    export_fair_materials_docx,
    get_funnel,
    get_snapshot,
    get_student_checkin,
    get_student_checkin_for_correction,
    issue_student_checkin_code,
    list_companies,
    list_fairs,
    register_company,
    review_company,
    transition_fair,
    update_fair,
)

router = APIRouter(prefix="/fairs", tags=["fairs"])

READ_ROLES = (
    Role.EMPLOYMENT_OFFICE,
    Role.COUNSELOR,
)
WRITE_ROLES = (Role.EMPLOYMENT_OFFICE,)
CHECKIN_ROLES = (Role.EMPLOYMENT_OFFICE, Role.COUNSELOR)


@router.get("", response_model=list[FairResponse], dependencies=[Depends(require_roles(*READ_ROLES))])
def list_fairs_endpoint(db: Session = Depends(get_db)) -> list[FairResponse]:
    return [_fair_response(row) for row in list_fairs(db)]


@router.post("", response_model=FairResponse, dependencies=[Depends(require_roles(*WRITE_ROLES))])
def create_fair_endpoint(
    payload: FairCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairResponse:
    try:
        row = create_fair(
            db,
            name=payload.name,
            fair_type=payload.fair_type,
            held_at=payload.held_at,
            location=payload.location,
            actor=user.username,
            idempotency_key=payload.idempotency_key,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _fair_response(row)


@router.put("/{fair_id}", response_model=FairResponse, dependencies=[Depends(require_roles(*WRITE_ROLES))])
def update_fair_endpoint(
    fair_id: int,
    payload: FairUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairResponse:
    try:
        row = update_fair(db, fair_id, actor=user.username, updates=payload.update_dict())
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _fair_response(row)


@router.put(
    "/{fair_id}/status",
    response_model=FairResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def update_fair_status_endpoint(
    fair_id: int,
    payload: FairStatusUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairResponse:
    try:
        row = transition_fair(
            db,
            fair_id,
            status=payload.status,
            actor=user.username,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _fair_response(row)


@router.post(
    "/{fair_id}/student-checkin-code",
    response_model=FairStudentCheckinCodeResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def issue_student_checkin_code_endpoint(
    fair_id: int,
    payload: FairStudentCheckinCodeIssue,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairStudentCheckinCodeResponse:
    try:
        code, issued_at = issue_student_checkin_code(
            db,
            fair_id,
            reason=payload.reason,
            idempotency_key=str(payload.idempotency_key),
            expected_issued_at=payload.expected_issued_at,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FairStudentCheckinCodeResponse(
        fair_id=fair_id,
        checkin_code=code,
        issued_at=issued_at,
    )


@router.delete("/{fair_id}", dependencies=[Depends(require_roles(*WRITE_ROLES))])
def delete_fair_endpoint(
    fair_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        delete_fair(db, fair_id, actor=user.username)
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get(
    "/{fair_id}/companies",
    response_model=list[FairCompanyResponse],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def companies_endpoint(fair_id: int, db: Session = Depends(get_db)) -> list[FairCompanyResponse]:
    try:
        return [_company_response(row) for row in list_companies(db, fair_id)]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{fair_id}/companies",
    response_model=FairCompanyResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def register_company_endpoint(
    fair_id: int,
    payload: FairCompanyCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairCompanyResponse:
    try:
        row = register_company(
            db,
            fair_id,
            company=payload.company,
            title=payload.title,
            raw_text=payload.raw_text,
            booth=payload.booth,
            actor=user.username,
            idempotency_key=payload.idempotency_key,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _company_response(row)


@router.put(
    "/{fair_id}/companies/{company_id}/review",
    response_model=FairCompanyResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def review_company_endpoint(
    fair_id: int,
    company_id: int,
    payload: FairCompanyReview,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairCompanyResponse:
    try:
        row = review_company(
            db,
            fair_id,
            company_id,
            status=payload.status,
            booth=payload.booth,
            actor=user.username,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _company_response(row)


@router.post(
    "/{fair_id}/checkins",
    response_model=FairCheckinResponse,
    dependencies=[Depends(require_roles(*CHECKIN_ROLES))],
)
def checkin_endpoint(
    fair_id: int,
    payload: FairCheckinCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairCheckinResponse:
    try:
        row = checkin_student(
            db,
            fair_id,
            student_no=payload.student_no,
            gate_id=payload.gate_id,
            actor=user.username,
            college_id=user.college_id if user.role is Role.COUNSELOR else None,
            idempotency_key=payload.idempotency_key,
        )
    except StudentOutsideCollegeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _checkin_response(row)


@router.get(
    "/{fair_id}/checkins/students/{student_no}",
    response_model=FairCheckinResponse,
    dependencies=[Depends(require_roles(*CHECKIN_ROLES))],
)
def checkin_result_endpoint(
    fair_id: int,
    student_no: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairCheckinResponse:
    try:
        row = get_student_checkin(
            db,
            fair_id,
            student_no=student_no,
            college_id=user.college_id if user.role is Role.COUNSELOR else None,
        )
    except StudentOutsideCollegeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _checkin_response(row)


@router.post(
    "/{fair_id}/checkins/corrections",
    response_model=FairCheckinResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def correct_checkin_endpoint(
    fair_id: int,
    payload: FairCheckinCorrection,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FairCheckinResponse:
    try:
        row = correct_student_checkin(
            db,
            fair_id,
            student_no=payload.student_no,
            gate_id=payload.gate_id,
            at=payload.at,
            reason=payload.reason,
            actor=user.username,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _checkin_response(row)


@router.get(
    "/{fair_id}/checkins/corrections/{student_no}",
    response_model=FairCheckinResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def correction_result_endpoint(
    fair_id: int,
    student_no: str,
    db: Session = Depends(get_db),
) -> FairCheckinResponse:
    try:
        row = get_student_checkin_for_correction(
            db,
            fair_id,
            student_no=student_no,
        )
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _checkin_response(row)


@router.get(
    "/{fair_id}/snapshot",
    response_model=FairSnapshotResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def snapshot_endpoint(fair_id: int, db: Session = Depends(get_db)) -> FairSnapshotResponse:
    try:
        return FairSnapshotResponse(**get_snapshot(db, fair_id))
    except FairConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/{fair_id}/funnel",
    response_model=FairFunnelResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def funnel_endpoint(fair_id: int, db: Session = Depends(get_db)) -> FairFunnelResponse:
    try:
        return FairFunnelResponse(**get_funnel(db, fair_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{fair_id}/materials.docx", dependencies=[Depends(require_roles(*READ_ROLES))])
def materials_endpoint(
    fair_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        body = export_fair_materials_docx(db, fair_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_db(
        db,
        actor=user.username,
        actor_id=user.user_id,
        actor_role=user.role.value,
        source_channel="management",
        correlation_id=f"FAIR-MATERIAL-{uuid4().hex[:16]}",
        action="fair.materials.export",
        object_type="fair",
        object_id=fair_id,
        change_summary={
            "scope": "recruitment_fair",
            "format": "docx",
            "content_recorded": False,
        },
    )
    db.commit()
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": chinese_attachment(
            f"招聘会{fair_id}会前材料.docx",
            fallback=f"fair-{fair_id}.docx",
        )},
    )


def _fair_response(row) -> FairResponse:
    return FairResponse(
        id=row.id,
        name=row.name,
        fair_type=row.fair_type,
        held_at=row.held_at,
        location=row.location,
        status=row.status,
        registration_opens_at=row.registration_opens_at,
        registration_closes_at=row.registration_closes_at,
        checkin_started_at=row.checkin_started_at,
        checkin_ended_at=row.checkin_ended_at,
        archived_at=row.archived_at,
        student_checkin_code_issued_at=row.student_checkin_code_issued_at,
    )


def _company_response(row) -> FairCompanyResponse:
    return FairCompanyResponse(
        id=row.id,
        fair_id=row.fair_id,
        company=row.company,
        booth=row.booth,
        status=row.status,
        title=row.title,
        job_id=row.job_id,
    )


def _checkin_response(row) -> FairCheckinResponse:
    return FairCheckinResponse(
        id=row.id,
        fair_id=row.fair_id,
        student_id=row.student_id,
        gate_id=row.gate_id,
        at=row.at,
        source=row.source,
        correction_reason=row.correction_reason,
    )
