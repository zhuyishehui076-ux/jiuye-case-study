"""Enterprise token, submission pipeline, and anonymous supply aggregates."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.core.config import get_settings
from app.core.crypto import decrypt_field
from app.db.session import SessionLocal
from app.llm.provider import Provider, get_provider
from app.models import (
    EnterpriseAccessToken,
    EnterpriseApplicationEvent,
    EnterpriseJobSubmission,
    EnterpriseOrganization,
    Job,
    JobAction,
    JobApplication,
    JobParse,
    Major,
    Match,
    OperationReceipt,
    Student,
)
from app.models.enums import EmploymentStatus, JobActionType, JobSource
from app.services.jobs import calculate_matches_for_job, parse_job

logger = logging.getLogger(__name__)

MIN_AGGREGATE_SIZE = 10
STATUS_QUEUED = "queued"
STATUS_PARSING = "parsing"
STATUS_REVIEW_PENDING = "review_pending"
STATUS_MATCHING = "matching"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = {STATUS_REVIEW_PENDING, STATUS_COMPLETED, STATUS_FAILED}
APPLICATION_TRANSITIONS = {
    "submitted": {"interview", "rejected"},
    "interview": {"offered", "rejected"},
    "offered": {"accepted", "rejected"},
}
APPLICATION_ACTION_TYPES = {
    "interview": JobActionType.INTERVIEW.value,
    "offered": JobActionType.OFFER.value,
    "accepted": JobActionType.ACCEPTED.value,
}


class EnterpriseRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: int):
        super().__init__("enterprise submission rate limit reached")
        self.retry_after_seconds = max(1, retry_after_seconds)


def enterprise_token_digest(raw_token: str) -> str:
    settings = get_settings()
    key = (settings.field_encryption_key or settings.jwt_secret).encode()
    return hmac.new(key, raw_token.encode(), hashlib.sha256).hexdigest()


def _request_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _advisory_lock(db: Session, namespace: str, key: str) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 61))"),
        {"key": f"{namespace}:{key}"},
    )


def generate_enterprise_token() -> str:
    """Generate a runtime credential; callers must never persist the plaintext."""
    return secrets.token_urlsafe(32)


def create_enterprise_access_token(
    db: Session,
    *,
    company_name: str,
    raw_token: str,
    expires_at: datetime,
    actor: str,
) -> EnterpriseAccessToken:
    if len(raw_token) < 24:
        raise ValueError("enterprise token must contain at least 24 characters")
    if expires_at <= datetime.now(UTC):
        raise ValueError("enterprise token expiry must be in the future")
    row = EnterpriseAccessToken(
        company_name=company_name.strip(),
        token_digest=enterprise_token_digest(raw_token),
        expires_at=expires_at,
        created_by=actor,
    )
    db.add(row)
    db.flush()
    record_db(
        db,
        actor=actor,
        action="enterprise_token.create",
        object_type="enterprise_access_token",
        object_id=row.id,
        change_summary={"company": row.company_name, "expires_at": expires_at.isoformat()},
    )
    db.commit()
    db.refresh(row)
    return row


def _remaining_submissions(row: EnterpriseAccessToken, now: datetime) -> int:
    limit = get_settings().enterprise_submit_limit_per_hour
    if (
        row.submit_window_started_at is None
        or row.submit_window_started_at <= now - timedelta(hours=1)
    ):
        return limit
    return max(0, limit - row.submit_count)


def enterprise_session_payload(
    db: Session,
    *,
    access_token_id: int,
) -> dict[str, Any]:
    row = db.get(EnterpriseAccessToken, access_token_id)
    if row is None:
        raise ValueError("enterprise token not found")
    settings = get_settings()
    return {
        "company": row.company_name,
        "expires_at": row.expires_at,
        "submit_limit_per_hour": settings.enterprise_submit_limit_per_hour,
        "remaining_submissions": _remaining_submissions(row, datetime.now(UTC)),
        "show_demo_badge": settings.show_demo_badge,
        "profile": "demo",
        "organization_id": None,
        "account_id": None,
    }


def enterprise_production_session_payload(
    db: Session,
    *,
    organization_id: int,
    account_id: int,
    expires_at: datetime,
) -> dict[str, Any]:
    organization = db.get(EnterpriseOrganization, organization_id)
    if organization is None:
        raise ValueError("enterprise organization not found")
    return {
        "company": organization.short_name or organization.legal_name,
        "expires_at": expires_at,
        "submit_limit_per_hour": None,
        "remaining_submissions": None,
        "show_demo_badge": False,
        "profile": "production",
        "organization_id": organization.id,
        "account_id": account_id,
    }


def enterprise_profile(db: Session, *, organization_id: int) -> dict[str, Any]:
    row = db.get(EnterpriseOrganization, organization_id)
    if row is None:
        raise ValueError("enterprise organization not found")
    return {
        "organization_id": row.id,
        "legal_name": row.legal_name,
        "short_name": row.short_name,
        "credit_code": row.credit_code,
        "industry": row.industry,
        "scale": row.scale,
        "city": row.city,
        "address": row.address,
        "website": row.website,
        "summary": row.summary,
        "contact_name": row.contact_name,
        "status": row.status,
        "revision": row.revision,
    }


def update_enterprise_profile(
    db: Session,
    *,
    organization_id: int,
    values: dict[str, Any],
    expected_revision: int,
    idempotency_key: UUID,
    actor: str,
) -> dict[str, Any]:
    key = str(idempotency_key)
    request_hash = _request_hash(
        {
            "organization_id": organization_id,
            "values": values,
            "expected_revision": expected_revision,
        }
    )
    _advisory_lock(db, "enterprise.profile.update", key)
    receipt = db.execute(
        select(OperationReceipt).where(
            OperationReceipt.namespace == "enterprise.profile.update",
            OperationReceipt.idempotency_key == key,
        )
    ).scalar_one_or_none()
    if receipt is not None:
        if receipt.request_hash != request_hash:
            raise ValueError("profile idempotency key reused for another request")
        return receipt.result_json
    row = db.execute(
        select(EnterpriseOrganization)
        .where(EnterpriseOrganization.id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise ValueError("enterprise organization not found")
    if row.revision != expected_revision:
        raise RuntimeError("enterprise profile has changed; reload before saving")
    before = enterprise_profile(db, organization_id=organization_id)
    for field in (
        "short_name",
        "industry",
        "scale",
        "city",
        "address",
        "website",
        "summary",
        "contact_name",
    ):
        if field in values:
            value = values[field]
            setattr(row, field, value.strip() if isinstance(value, str) else value)
    row.revision += 1
    db.flush()
    after = enterprise_profile(db, organization_id=organization_id)
    record_db(
        db,
        actor=actor,
        actor_role="enterprise",
        source_channel="enterprise",
        action="enterprise.profile.update",
        object_type="enterprise_organization",
        object_id=row.id,
        change_summary={"before": before, "after": after},
    )
    db.add(
        OperationReceipt(
            namespace="enterprise.profile.update",
            idempotency_key=key,
            request_hash=request_hash,
            result_json=after,
            created_by=actor,
        )
    )
    db.commit()
    return after


def _consume_submission_slot(
    db: Session,
    *,
    access_token_id: int,
    now: datetime,
) -> EnterpriseAccessToken:
    row = db.execute(
        select(EnterpriseAccessToken)
        .where(EnterpriseAccessToken.id == access_token_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None or not row.is_active or row.expires_at <= now:
        raise ValueError("enterprise token is inactive or expired")
    window = timedelta(hours=1)
    if row.submit_window_started_at is None or row.submit_window_started_at <= now - window:
        row.submit_window_started_at = now
        row.submit_count = 0
    limit = get_settings().enterprise_submit_limit_per_hour
    if row.submit_count >= limit:
        reset_at = row.submit_window_started_at + window
        raise EnterpriseRateLimitError(int((reset_at - now).total_seconds()))
    row.submit_count += 1
    row.last_used_at = now
    return row


def create_enterprise_submission(
    db: Session,
    *,
    access_token_id: int | None = None,
    organization_id: int | None = None,
    title: str,
    raw_text: str,
    idempotency_key: UUID | None = None,
) -> EnterpriseJobSubmission:
    now = datetime.now(UTC)
    if (access_token_id is None) == (organization_id is None):
        raise ValueError("exactly one enterprise owner is required")
    if idempotency_key is not None:
        key = str(idempotency_key)
        request_hash = _request_hash(
            {
                "access_token_id": access_token_id,
                "organization_id": organization_id,
                "title": title,
                "raw_text": raw_text,
            }
        )
        _advisory_lock(db, "enterprise.job.submit", key)
        receipt = db.execute(
            select(OperationReceipt).where(
                OperationReceipt.namespace == "enterprise.job.submit",
                OperationReceipt.idempotency_key == key,
            )
        ).scalar_one_or_none()
        if receipt is not None:
            if receipt.request_hash != request_hash:
                raise ValueError("job idempotency key reused for another request")
            replay_id = receipt.result_json.get("submission_id")
            replay = db.get(EnterpriseJobSubmission, replay_id)
            if replay is None:
                raise ValueError("job submission replay target disappeared")
            db.commit()
            return replay
    token: EnterpriseAccessToken | None = None
    organization: EnterpriseOrganization | None = None
    if access_token_id is not None:
        token = _consume_submission_slot(db, access_token_id=access_token_id, now=now)
        actor = f"enterprise-demo:{token.id}"
        company_name = token.company_name
    else:
        organization = db.execute(
            select(EnterpriseOrganization)
            .where(EnterpriseOrganization.id == organization_id)
            .with_for_update(read=True, key_share=True)
        ).scalar_one_or_none()
        if organization is None or organization.status != "verified":
            raise ValueError("enterprise organization is not active")
        actor = f"enterprise:{organization.id}"
        company_name = organization.short_name or organization.legal_name
    job = Job(
        enterprise_organization_id=organization.id if organization is not None else None,
        company=company_name,
        title=title.strip(),
        raw_text=raw_text.strip(),
        source=JobSource.FORM.value,
        received_at=now,
        created_by=actor,
    )
    db.add(job)
    db.flush()
    submission = EnterpriseJobSubmission(
        access_token_id=token.id if token is not None else None,
        organization_id=organization.id if organization is not None else None,
        job_id=job.id,
        status=STATUS_QUEUED,
        created_by=actor,
    )
    db.add(submission)
    db.flush()
    if idempotency_key is not None:
        db.add(
            OperationReceipt(
                namespace="enterprise.job.submit",
                idempotency_key=str(idempotency_key),
                request_hash=request_hash,
                result_json={"submission_id": submission.id, "job_id": job.id},
                created_by=actor,
            )
        )
    record_db(
        db,
        actor=actor,
        action="enterprise_job.submit",
        object_type="enterprise_job_submission",
        object_id=submission.id,
        change_summary={
            "job_id": job.id,
            "title": job.title,
            "organization_id": organization.id if organization is not None else None,
        },
    )
    db.commit()
    db.refresh(submission)
    return submission


def _safe_match_count(count: int) -> tuple[int | None, bool]:
    if 0 < count < MIN_AGGREGATE_SIZE:
        return None, True
    return count, False


def _submission_payload(
    submission: EnterpriseJobSubmission,
    job: Job,
    parse: JobParse | None,
    match_count: int,
    pushed_count: int,
    feedback_count: int,
) -> dict[str, Any]:
    visible_count, suppressed = _safe_match_count(match_count)
    visible_recommendation = None if suppressed else pushed_count > 0
    visible_feedback = None if suppressed else feedback_count > 0
    return {
        "submission_id": submission.id,
        "job_id": job.id,
        "company": job.company,
        "title": job.title,
        "status": submission.status,
        "parse_confidence": (
            float(parse.parse_confidence)
            if parse is not None and parse.parse_confidence is not None
            else None
        ),
        "degree_req": parse.degree_req if parse is not None else None,
        "major_codes": list(parse.major_codes) if parse is not None else [],
        "direction_keywords": list(parse.direction_keywords) if parse is not None else [],
        "location": parse.location if parse is not None else None,
        "matched_candidate_count": visible_count,
        "small_group_suppressed": suppressed,
        "recommendation_started": visible_recommendation,
        "student_feedback_received": visible_feedback,
        "submitted_at": submission.created_at,
        "updated_at": submission.updated_at,
        "error_message": (
            "处理暂不可用，请联系就业服务人员" if submission.status == STATUS_FAILED else None
        ),
    }


def list_enterprise_submissions(
    db: Session,
    *,
    access_token_id: int | None = None,
    organization_id: int | None = None,
) -> list[dict[str, Any]]:
    if (access_token_id is None) == (organization_id is None):
        raise ValueError("exactly one enterprise owner is required")
    counts = (
        select(
            Match.job_id.label("job_id"),
            func.count(Match.id).filter(Match.score > 0).label("match_count"),
            func.count(Match.id).filter(Match.pushed_at.is_not(None)).label("pushed_count"),
            func.count(Match.id).filter(Match.feedback.is_not(None)).label("feedback_count"),
        )
        .group_by(Match.job_id)
        .subquery()
    )
    statement = (
        select(
            EnterpriseJobSubmission,
            Job,
            JobParse,
            func.coalesce(counts.c.match_count, 0),
            func.coalesce(counts.c.pushed_count, 0),
            func.coalesce(counts.c.feedback_count, 0),
        )
        .join(Job, EnterpriseJobSubmission.job_id == Job.id)
        .outerjoin(JobParse, JobParse.job_id == Job.id)
        .outerjoin(counts, counts.c.job_id == Job.id)
        .order_by(EnterpriseJobSubmission.id.desc())
    )
    if organization_id is not None:
        statement = statement.where(EnterpriseJobSubmission.organization_id == organization_id)
    else:
        statement = statement.where(EnterpriseJobSubmission.access_token_id == access_token_id)
    rows = db.execute(statement).all()
    return [
        _submission_payload(
            submission,
            job,
            parse,
            int(match_count),
            int(pushed_count),
            int(feedback_count),
        )
        for submission, job, parse, match_count, pushed_count, feedback_count in rows
    ]


def process_enterprise_submission(
    submission_id: int,
    provider: Provider | None = None,
) -> dict[str, Any] | None:
    """Run the shared T2 parser and T4 matcher in a fresh background session."""
    with SessionLocal() as db:
        submission = db.execute(
            select(EnterpriseJobSubmission)
            .where(EnterpriseJobSubmission.id == submission_id)
            .with_for_update()
        ).scalar_one_or_none()
        if submission is None:
            raise ValueError("enterprise submission not found")
        if submission.status != STATUS_QUEUED:
            return None
        submission.status = STATUS_PARSING
        submission.started_at = datetime.now(UTC)
        submission.failure_detail = None
        db.commit()

        try:
            active_provider = provider or get_provider()
            parsed = parse_job(
                db,
                submission.job_id,
                active_provider,
                actor=submission.created_by or f"enterprise:{submission.access_token_id}",
            )
            submission = db.get(EnterpriseJobSubmission, submission_id)
            if submission is None:
                raise RuntimeError("enterprise submission disappeared")
            if parsed.needs_review:
                submission.status = STATUS_REVIEW_PENDING
                submission.completed_at = datetime.now(UTC)
                record_db(
                    db,
                    actor=submission.created_by or "enterprise-worker",
                    action="enterprise_job.review_pending",
                    object_type="enterprise_job_submission",
                    object_id=submission.id,
                    change_summary={"job_id": submission.job_id},
                )
                db.commit()
                return None

            submission.status = STATUS_MATCHING
            db.commit()
            calculate_matches_for_job(
                db,
                submission.job_id,
                actor=submission.created_by or "enterprise-worker",
            )
            db.commit()
            submission = db.get(EnterpriseJobSubmission, submission_id)
            if submission is None:
                raise RuntimeError("enterprise submission disappeared")
            submission.status = STATUS_COMPLETED
            submission.completed_at = datetime.now(UTC)
            record_db(
                db,
                actor=submission.created_by or "enterprise-worker",
                action="enterprise_job.complete",
                object_type="enterprise_job_submission",
                object_id=submission.id,
                change_summary={"job_id": submission.job_id},
            )
            db.commit()
            return list_enterprise_submissions(
                db,
                access_token_id=submission.access_token_id,
                organization_id=submission.organization_id,
            )[0]
        except Exception as exc:
            db.rollback()
            submission = db.get(EnterpriseJobSubmission, submission_id)
            if submission is None:
                raise
            submission.status = STATUS_FAILED
            submission.completed_at = datetime.now(UTC)
            submission.failure_detail = f"{type(exc).__name__}: {exc}"[:2000]
            record_db(
                db,
                actor=submission.created_by or "enterprise-worker",
                action="enterprise_job.failed",
                object_type="enterprise_job_submission",
                object_id=submission.id,
                change_summary={"job_id": submission.job_id, "error_type": type(exc).__name__},
            )
            db.commit()
            logger.exception("enterprise submission %s failed", submission_id)
            return None


def _share(count: int, total: int) -> float:
    return round(count * 100 / total, 1) if total else 0.0


def list_enterprise_candidates(
    db: Session,
    *,
    organization_id: int,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    statement = (
        select(JobApplication, Job, Student, Major)
        .join(Job, Job.id == JobApplication.job_id)
        .join(Student, Student.id == JobApplication.student_id)
        .join(Major, Major.id == Student.major_id)
        .where(
            Job.enterprise_organization_id == organization_id,
            JobApplication.enterprise_disclosure_authorized_at.is_not(None),
            JobApplication.enterprise_disclosure_revoked_at.is_(None),
            JobApplication.status != "withdrawn",
        )
    )
    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = db.execute(
        statement.order_by(JobApplication.submitted_at.desc(), JobApplication.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        {
            "application_id": application.id,
            "job_id": job.id,
            "job_title": job.title,
            "name": student.name,
            "major_name": major.name,
            "degree_level": student.degree_level,
            "cohort_year": student.cohort_year,
            "contact": decrypt_field(student.contact_encrypted)
            if student.contact_encrypted
            else None,
            "status": application.status,
            "submitted_at": application.submitted_at,
            "revision": application.revision,
        }
        for application, job, student, major in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def transition_enterprise_application(
    db: Session,
    *,
    organization_id: int,
    account_id: int,
    application_id: int,
    target_status: str,
    reason: str,
    revision: int,
    idempotency_key: UUID,
) -> dict[str, Any]:
    key = str(idempotency_key)
    request_hash = _request_hash(
        {
            "organization_id": organization_id,
            "account_id": account_id,
            "application_id": application_id,
            "target_status": target_status,
            "reason": reason,
            "revision": revision,
        }
    )
    _advisory_lock(db, "enterprise.application.transition", key)
    replay = db.execute(
        select(EnterpriseApplicationEvent).where(
            EnterpriseApplicationEvent.idempotency_key == key
        )
    ).scalar_one_or_none()
    if replay is not None:
        if replay.request_hash != request_hash:
            raise RuntimeError("application idempotency key reused for another request")
        db.commit()
        return {
            "application_id": replay.application_id,
            "status": replay.to_status,
            "previous_status": replay.from_status,
            "revision": revision + 1,
            "already_applied": True,
        }
    row = db.execute(
        select(JobApplication, Job)
        .join(Job, Job.id == JobApplication.job_id)
        .where(JobApplication.id == application_id)
        .with_for_update(of=JobApplication)
    ).one_or_none()
    if row is None:
        raise ValueError("application not found")
    application, job = row
    if job.enterprise_organization_id != organization_id:
        raise PermissionError("enterprise cannot access another tenant application")
    if (
        application.enterprise_disclosure_authorized_at is None
        or application.enterprise_disclosure_revoked_at is not None
    ):
        raise PermissionError("student disclosure is not active")
    if application.revision != revision:
        raise RuntimeError("application has changed; reload before updating")
    allowed = APPLICATION_TRANSITIONS.get(application.status, set())
    if target_status not in allowed:
        raise RuntimeError("invalid application status transition")
    previous = application.status
    now = datetime.now(UTC)
    application.status = target_status
    application.status_updated_at = now
    application.revision += 1
    event = EnterpriseApplicationEvent(
        organization_id=organization_id,
        account_id=account_id,
        application_id=application.id,
        from_status=previous,
        to_status=target_status,
        reason=reason.strip(),
        idempotency_key=key,
        request_hash=request_hash,
        created_by=f"enterprise:{organization_id}",
    )
    db.add(event)
    db.flush()
    progress_action: JobAction | None = None
    action_type = APPLICATION_ACTION_TYPES.get(target_status)
    if action_type is not None:
        application_source = db.execute(
            select(JobAction.source).where(
                JobAction.application_id == application.id,
                JobAction.type == JobActionType.APPLY.value,
            )
            .order_by(JobAction.id)
            .limit(1)
        ).scalar_one_or_none()
        progress_action = JobAction(
            student_id=application.student_id,
            type=action_type,
            target=f"{job.company} · {job.title}"[:128],
            occurred_at=now,
            source=(
                application_source
                if application_source and application_source.startswith("fair:")
                else "企业正式候选流程"
            ),
            created_by=f"enterprise:{organization_id}",
        )
        db.add(progress_action)
        db.flush()
    record_db(
        db,
        actor=f"enterprise:{organization_id}",
        actor_role="enterprise",
        source_channel="enterprise",
        correlation_id=f"ENT-APP-{event.id}",
        action="enterprise.application.transition",
        object_type="job_application",
        object_id=application.id,
        change_summary={
            "organization_id": organization_id,
            "account_id": account_id,
            "from": previous,
            "to": target_status,
            "reason": reason.strip(),
            "revision": application.revision,
            "job_action_id": progress_action.id if progress_action else None,
            "job_action_type": progress_action.type if progress_action else None,
            "job_action_source": progress_action.source if progress_action else None,
        },
    )
    db.commit()
    return {
        "application_id": application.id,
        "status": application.status,
        "previous_status": previous,
        "revision": application.revision,
        "already_applied": False,
    }


def enterprise_supply_report(db: Session) -> dict[str, Any]:
    """Return only aggregates whose source group has at least ten students."""
    major_count = func.count(Student.id)
    major_rows = db.execute(
        select(Major.name, major_count)
        .join(Student, Student.major_id == Major.id)
        .group_by(Major.id, Major.name)
        .having(major_count >= MIN_AGGREGATE_SIZE)
        .order_by(major_count.desc(), Major.name)
    ).all()
    degree_count = func.count(Student.id)
    degree_rows = db.execute(
        select(Student.degree_level, degree_count)
        .group_by(Student.degree_level)
        .having(degree_count >= MIN_AGGREGATE_SIZE)
        .order_by(degree_count.desc(), Student.degree_level)
    ).all()
    cohort_count = func.count(Student.id)
    converted_count = func.sum(
        case(
            (Student.status != EmploymentStatus.UNSETTLED.value, 1),
            else_=0,
        )
    )
    cohort_rows = db.execute(
        select(Student.cohort_year, cohort_count, converted_count)
        .group_by(Student.cohort_year)
        .having(cohort_count >= MIN_AGGREGATE_SIZE)
        .order_by(Student.cohort_year)
    ).all()

    visible_major_rows = [
        (label, int(count))
        for label, count in major_rows
        if int(count) >= MIN_AGGREGATE_SIZE
    ]
    visible_degree_rows = [
        (label, int(count))
        for label, count in degree_rows
        if int(count) >= MIN_AGGREGATE_SIZE
    ]
    visible_student_count = sum(count for _, count in visible_major_rows)
    visible_degree_count = sum(count for _, count in visible_degree_rows)
    majors = [
        {"label": label, "count": count, "share": _share(count, visible_student_count)}
        for label, count in visible_major_rows
    ]
    degrees = [
        {"label": label, "count": count, "share": _share(count, visible_degree_count)}
        for label, count in visible_degree_rows
    ]
    historical = [
        {
            "period": str(period),
            "total": int(count),
            "converted": int(converted or 0),
            "conversion_rate": _share(int(converted or 0), int(count)),
        }
        for period, count, converted in cohort_rows
        if int(count) >= MIN_AGGREGATE_SIZE
    ]
    return {
        "as_of": datetime.now(UTC),
        "total_students": visible_student_count,
        "minimum_group_size": MIN_AGGREGATE_SIZE,
        "majors": majors,
        "degrees": degrees,
        "historical_conversion": historical,
    }
