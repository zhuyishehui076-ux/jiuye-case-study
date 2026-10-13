"""T10 M7 company visit archives and conversion dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.models import CompanyVisit, Job, JobAction
from app.models.enums import JobActionType
from app.services.write_idempotency import (
    WriteOperationConflict,
    validate_write_operation_key,
    write_request_hash,
)


@dataclass(frozen=True)
class VisitDashboardRow:
    id: int
    company: str
    visitors: str | None
    visited_on: date
    promised_jobs: int
    company_visit_count: int
    company_promised_jobs: int
    submitted_jds: int
    hires: int
    jd_conversion_rate: float
    hire_conversion_rate: float
    follow_status: str | None
    note: str | None


@dataclass(frozen=True)
class VisitDashboardSummary:
    visit_count: int
    promised_jobs: int
    submitted_jds: int
    hires: int
    jd_conversion_rate: float
    hire_conversion_rate: float


@dataclass(frozen=True)
class VisitDashboard:
    summary: VisitDashboardSummary
    rows: list[VisitDashboardRow]


def _promised_jobs(visit: CompanyVisit) -> int:
    commitments = visit.commitments_json or {}
    raw = commitments.get("promised_jobs", commitments.get("promised_headcount", 0))
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def create_visit(
    db: Session,
    *,
    company: str,
    visitors: str | None,
    visited_on: date,
    promised_jobs: int,
    follow_status: str | None,
    note: str | None,
    actor: str,
    idempotency_key: str,
    actor_id: int | None = None,
    actor_role: str | None = None,
) -> tuple[CompanyVisit, bool]:
    idempotency_key = validate_write_operation_key(idempotency_key)
    normalized_promised_jobs = max(promised_jobs, 0)
    request_hash = write_request_hash(
        "company_visit.create",
        actor=actor,
        payload={
            "company": company,
            "follow_status": follow_status,
            "note": note,
            "promised_jobs": normalized_promised_jobs,
            "visited_on": visited_on.isoformat(),
            "visitors": visitors,
        },
    )
    existing = _find_visit_by_operation(db, idempotency_key)
    if existing is not None:
        _assert_same_operation(existing.request_hash, request_hash)
        return existing, True

    visit = CompanyVisit(
        company=company,
        visitors=visitors,
        visited_on=visited_on,
        commitments_json={"promised_jobs": normalized_promised_jobs},
        follow_status=follow_status,
        note=note,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        created_by=actor,
    )
    db.add(visit)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _find_visit_by_operation(db, idempotency_key)
        if existing is None:
            raise
        _assert_same_operation(existing.request_hash, request_hash)
        return existing, True
    record_db(
        db,
        actor=actor,
        actor_id=actor_id,
        actor_role=actor_role,
        source_channel="management.visits",
        correlation_id=f"COMPANY-VISIT-{idempotency_key}",
        action="company_visit.create",
        object_type="company_visit",
        object_id=visit.id,
        change_summary={
            "after": {
                "company": company,
                "visited_on": visited_on.isoformat(),
                "promised_jobs": normalized_promised_jobs,
                "follow_status": follow_status,
            }
        },
    )
    db.commit()
    db.refresh(visit)
    return visit, False


def get_visit_by_operation(
    db: Session,
    idempotency_key: str,
    *,
    actor: str,
) -> CompanyVisit | None:
    idempotency_key = validate_write_operation_key(idempotency_key)
    return db.scalar(
        select(CompanyVisit).where(
            CompanyVisit.idempotency_key == idempotency_key,
            CompanyVisit.created_by == actor,
        )
    )


def _find_visit_by_operation(
    db: Session,
    idempotency_key: str,
) -> CompanyVisit | None:
    """Resolve a replay globally; callers must verify its request hash."""

    return db.scalar(
        select(CompanyVisit).where(CompanyVisit.idempotency_key == idempotency_key)
    )


def _assert_same_operation(actual_hash: str | None, expected_hash: str) -> None:
    if actual_hash != expected_hash:
        raise WriteOperationConflict(
            "Idempotency-Key was already used for a different company visit request"
        )


def list_visits(db: Session) -> list[CompanyVisit]:
    return list(
        db.execute(
            select(CompanyVisit).order_by(CompanyVisit.visited_on.desc(), CompanyVisit.id.desc())
        ).scalars()
    )


def _counts_by_company(db: Session, companies: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    if not companies:
        return {}, {}
    submitted = {
        company: int(count)
        for company, count in db.execute(
            select(Job.company, func.count(Job.id))
            .where(Job.company.in_(companies))
            .group_by(Job.company)
        )
    }
    hires = {
        target: int(count)
        for target, count in db.execute(
            select(JobAction.target, func.count(JobAction.id))
            .where(JobAction.type == JobActionType.ACCEPTED.value)
            .where(JobAction.target.in_(companies))
            .group_by(JobAction.target)
        )
        if target is not None
    }
    return submitted, hires


def build_visit_dashboard(db: Session, *, companies: list[str] | None = None) -> VisitDashboard:
    stmt = select(CompanyVisit)
    if companies is not None:
        stmt = stmt.where(CompanyVisit.company.in_(companies))
    visits = list(
        db.execute(stmt.order_by(CompanyVisit.visited_on.desc(), CompanyVisit.id.desc())).scalars()
    )
    company_names = list({visit.company for visit in visits})
    submitted_by_company, hires_by_company = _counts_by_company(db, company_names)
    visits_by_company: dict[str, int] = {}
    promised_by_company: dict[str, int] = {}
    for visit in visits:
        visits_by_company[visit.company] = visits_by_company.get(visit.company, 0) + 1
        promised_by_company[visit.company] = (
            promised_by_company.get(visit.company, 0) + _promised_jobs(visit)
        )

    rows: list[VisitDashboardRow] = []
    for visit in visits:
        promised = _promised_jobs(visit)
        company_promised = promised_by_company.get(visit.company, 0)
        submitted = submitted_by_company.get(visit.company, 0)
        hires = hires_by_company.get(visit.company, 0)
        rows.append(
            VisitDashboardRow(
                id=visit.id,
                company=visit.company,
                visitors=visit.visitors,
                visited_on=visit.visited_on,
                promised_jobs=promised,
                company_visit_count=visits_by_company.get(visit.company, 0),
                company_promised_jobs=company_promised,
                submitted_jds=submitted,
                hires=hires,
                jd_conversion_rate=_rate(submitted, company_promised),
                hire_conversion_rate=_rate(hires, company_promised),
                follow_status=visit.follow_status,
                note=visit.note,
            )
        )

    promised_total = sum(row.promised_jobs for row in rows)
    # Job and hire facts are company-level, not visit-level. Count each company
    # once in the summary even when several visit archive rows share a company.
    submitted_total = sum(submitted_by_company.values())
    hires_total = sum(hires_by_company.values())
    return VisitDashboard(
        summary=VisitDashboardSummary(
            visit_count=len(rows),
            promised_jobs=promised_total,
            submitted_jds=submitted_total,
            hires=hires_total,
            jd_conversion_rate=_rate(submitted_total, promised_total),
            hire_conversion_rate=_rate(hires_total, promised_total),
        ),
        rows=rows,
    )
