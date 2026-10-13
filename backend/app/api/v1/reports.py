"""T11 X3 report export API."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.audit import record_db
from app.core.rbac import Role
from app.db.session import get_db
from app.services.downloads import chinese_attachment
from app.services.reports import (
    ReportMetrics,
    build_material_docx,
    build_monthly_xlsx,
    build_weekly_docx,
    collect_report_metrics,
)

router = APIRouter(prefix="/reports", tags=["reports"])

READ_ROLES = (Role.ADMIN, Role.EMPLOYMENT_OFFICE, Role.COUNSELOR, Role.LEADER_READONLY)
DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _college_scope(user: CurrentUser) -> int | None:
    return user.college_id if user.role is Role.COUNSELOR else None


def _export_response(
    db: Session,
    *,
    user: CurrentUser,
    report_type: str,
    filename: str,
    media_type: str,
    metrics: ReportMetrics,
    builder: Callable[[ReportMetrics], bytes],
) -> Response:
    content = builder(metrics)
    record_db(
        db,
        actor=user.username,
        action="report.export",
        object_type="report",
        object_id=report_type,
        change_summary={
            "report_type": report_type,
            "period_start": metrics.period_start.isoformat(),
            "period_end": metrics.period_end.isoformat(),
        },
    )
    db.commit()
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": chinese_attachment(filename, fallback="report" + filename[filename.rfind('.'):])},
    )


@router.get(
    "/weekly.docx",
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def weekly_report(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    metrics = collect_report_metrics(db, days=7, college_id=_college_scope(user))
    return _export_response(
        db,
        user=user,
        report_type="weekly",
        filename="就业工作周报.docx",
        media_type=DOCX_MEDIA,
        metrics=metrics,
        builder=lambda report_metrics: build_weekly_docx(
            report_metrics,
            audience=user.role.value,
        ),
    )


@router.get(
    "/monthly.xlsx",
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def monthly_report(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    metrics = collect_report_metrics(db, days=30, college_id=_college_scope(user))
    return _export_response(
        db,
        user=user,
        report_type="monthly",
        filename="就业工作月报.xlsx",
        media_type=XLSX_MEDIA,
        metrics=metrics,
        builder=build_monthly_xlsx,
    )


@router.get(
    "/material.docx",
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def material_report(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    metrics = collect_report_metrics(db, days=30, college_id=_college_scope(user))
    return _export_response(
        db,
        user=user,
        report_type="material",
        filename="就业工作汇报材料.docx",
        media_type=DOCX_MEDIA,
        metrics=metrics,
        builder=lambda report_metrics: build_material_docx(
            report_metrics,
            audience=user.role.value,
        ),
    )
