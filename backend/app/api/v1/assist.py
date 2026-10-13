"""T6 M2 一生一策帮扶台账 API。"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.audit import record_db
from app.core.rbac import Role
from app.db.session import get_db
from app.schemas.assist import (
    AssistCaseResponse,
    AssistRecordCreate,
    AssistRecordResponse,
)
from app.services.assist import (
    AssistRecordIdempotencyConflict,
    append_assist_record,
    export_assist_docx,
    export_assist_xlsx,
    list_assist_cases,
)
from app.services.assist_idempotency import (
    InvalidAssistIdempotencyKey,
)
from app.services.downloads import chinese_attachment

router = APIRouter(prefix="/assist", tags=["assist"])

READ_ROLES = (
    Role.EMPLOYMENT_OFFICE,
    Role.COUNSELOR,
)
WRITE_ROLES = (Role.EMPLOYMENT_OFFICE, Role.COUNSELOR)


def _scope_college(user: CurrentUser) -> int | None:
    return user.college_id if user.role is Role.COUNSELOR else None


def _record_export(
    db: Session,
    *,
    user: CurrentUser,
    file_format: str,
    row_count: int,
) -> None:
    college_id = _scope_college(user)
    object_id = f"college:{college_id}" if college_id is not None else "school"
    record_db(
        db,
        actor=user.username,
        actor_id=user.user_id,
        actor_role=user.role.value,
        source_channel="management-web",
        correlation_id=f"ASSIST-EXPORT-{uuid4().hex[:12]}",
        action="assist.export",
        object_type="assist_scope",
        object_id=object_id,
        change_summary={
            "format": file_format,
            "row_count": row_count,
            "college_scope": college_id,
        },
    )
    db.commit()


@router.get(
    "/cases",
    response_model=list[AssistCaseResponse],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_cases(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AssistCaseResponse]:
    return [
        AssistCaseResponse(**row)
        for row in list_assist_cases(db, college_id=_scope_college(user))
    ]


@router.post(
    "/cases/{case_id}/records",
    response_model=AssistRecordResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def append_record(
    case_id: int,
    payload: AssistRecordCreate,
    idempotency_key_header: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AssistRecordResponse:
    try:
        row, replayed = append_assist_record(
            db,
            case_id,
            action_type=payload.action_type,
            note=payload.note,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            idempotency_key=idempotency_key_header,
            college_id=_scope_college(user),
        )
    except InvalidAssistIdempotencyKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AssistRecordIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        if "forbidden" in message:
            raise HTTPException(status_code=403, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    return AssistRecordResponse(
        id=row.id,
        case_id=row.case_id,
        action_type=row.action_type,
        note=row.note,
        actor=row.actor,
        occurred_at=row.occurred_at,
        replayed=replayed,
    )


@router.get(
    "/export.xlsx",
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def export_xlsx(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    rows = list_assist_cases(db, college_id=_scope_college(user))
    content = export_assist_xlsx(rows)
    _record_export(db, user=user, file_format="xlsx", row_count=len(rows))
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": chinese_attachment("一生一策帮扶台账.xlsx", fallback="assist.xlsx")},
    )


@router.get(
    "/export.docx",
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def export_docx(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    rows = list_assist_cases(db, college_id=_scope_college(user))
    content = export_assist_docx(rows)
    _record_export(db, user=user, file_format="docx", row_count=len(rows))
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": chinese_attachment("一生一策帮扶记录.docx", fallback="assist.docx")},
    )
