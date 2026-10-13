"""S1 student-scope service layer; every read/write rechecks the token subject."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.audit import record_db
from app.core.config import get_settings
from app.core.security import verify_password
from app.models import (
    Job,
    JobAction,
    Match,
    Profile,
    Student,
    StudentBinding,
    StudentConsent,
)
from app.models.enums import JobActionType
from app.services.student_feedback import public_feedback, stored_feedback
from app.services.student_visibility import (
    DEFAULT_STUDENT_SUGGESTIONS,
    safe_profile_dimensions,
    safe_recommendation_reason,
    safe_student_suggestions,
)


class StudentForbiddenError(RuntimeError):
    pass


class StudentNotFoundError(RuntimeError):
    pass


def binding_code_digest(binding_code: str) -> str:
    key = (get_settings().field_encryption_key or get_settings().jwt_secret).encode()
    return hmac.new(key, binding_code.encode(), hashlib.sha256).hexdigest()


def authenticate_binding(db: Session, binding_code: str) -> tuple[StudentBinding, Student]:
    row = db.execute(
        select(StudentBinding, Student)
        .join(Student, StudentBinding.student_id == Student.id)
        .where(
            StudentBinding.binding_code_digest == binding_code_digest(binding_code),
            StudentBinding.is_active.is_(True),
        )
    ).one_or_none()
    if row is None or not verify_password(binding_code, row[0].binding_code_hash):
        raise StudentNotFoundError("invalid binding code")
    return row


def ensure_self(*, requested_student_id: int, token_student_id: int) -> None:
    if requested_student_id != token_student_id:
        raise StudentForbiddenError("student scope only permits own data")


def get_or_create_consent(
    db: Session,
    student_id: int,
    *,
    create_if_missing: bool = True,
) -> StudentConsent | None:
    consent = db.execute(
        select(StudentConsent).where(StudentConsent.student_id == student_id)
    ).scalar_one_or_none()
    if consent is None and create_if_missing:
        consent = StudentConsent(student_id=student_id, created_by=f"student:{student_id}")
        db.add(consent)
        db.flush()
    return consent


def recommendations(
    db: Session,
    *,
    requested_student_id: int,
    token_student_id: int,
) -> dict:
    ensure_self(
        requested_student_id=requested_student_id,
        token_student_id=token_student_id,
    )
    consent = get_or_create_consent(db, token_student_id)
    assert consent is not None
    if not consent.recommendation_enabled:
        return {"enabled": False, "items": []}
    rows = db.execute(
        select(Match, Job)
        .join(Job, Match.job_id == Job.id)
        .where(
            Match.student_id == token_student_id,
            Match.pushed_at.is_not(None),
        )
        .order_by(Match.pushed_at.desc(), Match.score.desc())
    ).all()
    return {
        "enabled": True,
        "items": [
            {
                "match_id": match.id,
                "job_id": job.id,
                "company": job.company,
                "title": job.title,
                "score": float(match.score),
                "reason_text": safe_recommendation_reason(match.reason_text),
                "feedback": public_feedback(match.feedback),
                "pushed_at": match.pushed_at,
            }
            for match, job in rows
        ],
    }


def update_match_feedback(
    db: Session,
    *,
    requested_student_id: int,
    token_student_id: int,
    match_id: int,
    feedback: str,
) -> Match:
    ensure_self(
        requested_student_id=requested_student_id,
        token_student_id=token_student_id,
    )
    match = db.execute(
        select(Match).where(Match.id == match_id, Match.student_id == token_student_id)
    ).scalar_one_or_none()
    if match is None or match.pushed_at is None:
        raise StudentNotFoundError("pushed match not found")
    stored = stored_feedback(feedback)
    before = public_feedback(match.feedback)
    if match.feedback == stored and match.feedback_at is not None:
        return match
    match.feedback = stored
    match.feedback_at = datetime.now(UTC)
    record_db(
        db,
        actor=f"student:{token_student_id}",
        action="student.match.feedback",
        object_type="match",
        object_id=match.id,
        change_summary={"before": before, "after": feedback},
    )
    db.commit()
    db.refresh(match)
    return match


def growth(
    db: Session,
    *,
    requested_student_id: int,
    token_student_id: int,
) -> dict:
    ensure_self(
        requested_student_id=requested_student_id,
        token_student_id=token_student_id,
    )
    student = db.execute(
        select(Student)
        .options(joinedload(Student.major))
        .where(Student.id == token_student_id)
    ).scalar_one_or_none()
    if student is None:
        raise StudentNotFoundError("student not found")
    consent = get_or_create_consent(
        db,
        token_student_id,
        create_if_missing=get_settings().student_demo_auth_enabled,
    )
    if consent is None:
        return {
            "enabled": False,
            "student_id": student.id,
            "name": student.name,
            "major_name": student.major.name,
            "term": None,
            "six_dims": {},
            "summary": "尚未完成成长数据用途授权",
            "suggestions": [],
        }
    if not consent.growth_profile_enabled:
        return {
            "enabled": False,
            "student_id": student.id,
            "name": student.name,
            "major_name": student.major.name,
            "term": None,
            "six_dims": {},
            "summary": "成长数据用途授权已关闭",
            "suggestions": [],
        }
    profile = db.execute(
        select(Profile)
        .where(Profile.student_id == token_student_id)
        .order_by(Profile.term.desc())
    ).scalars().first()
    if profile is None:
        return {
            "enabled": True,
            "student_id": student.id,
            "name": student.name,
            "major_name": student.major.name,
            "term": None,
            "six_dims": {},
            "summary": "成长画像正在准备中",
            "suggestions": list(DEFAULT_STUDENT_SUGGESTIONS),
        }
    dimensions = safe_profile_dimensions(profile.six_dims_json)
    strengths = [key for key, _value in sorted(dimensions.items(), key=lambda item: item[1], reverse=True)[:2]]
    summary = (
        f"当前优势集中在{'、'.join(strengths)}，继续通过行动记录积累成长证据。"
        if strengths
        else "持续记录学习、实践与求职进展，成长画像会随数据更新。"
    )
    return {
        "enabled": True,
        "student_id": student.id,
        "name": student.name,
        "major_name": student.major.name,
        "term": profile.term,
        "six_dims": dimensions,
        "summary": summary,
        "suggestions": safe_student_suggestions(
            profile.evidence_json.get("suggestions")
        ),
    }


def create_job_action(
    db: Session,
    *,
    requested_student_id: int,
    token_student_id: int,
    company: str,
    job_title: str,
    occurred_at: datetime | None,
) -> JobAction:
    ensure_self(
        requested_student_id=requested_student_id,
        token_student_id=token_student_id,
    )
    consent = get_or_create_consent(db, token_student_id)
    assert consent is not None
    if not consent.job_action_tracking_enabled:
        raise StudentForbiddenError("job action tracking consent is disabled")
    target = f"{company.strip()} · {job_title.strip()}"
    action = JobAction(
        student_id=token_student_id,
        type=JobActionType.APPLY.value,
        target=target,
        occurred_at=occurred_at or datetime.now(UTC),
        source="学生自报",
        created_by=f"student:{token_student_id}",
    )
    db.add(action)
    db.flush()
    record_db(
        db,
        actor=f"student:{token_student_id}",
        action="student.job_action.create",
        object_type="job_action",
        object_id=action.id,
        change_summary={"type": action.type, "target": target},
    )
    db.commit()
    db.refresh(action)
    return action


def list_job_actions(
    db: Session,
    *,
    requested_student_id: int,
    token_student_id: int,
) -> list[JobAction]:
    ensure_self(
        requested_student_id=requested_student_id,
        token_student_id=token_student_id,
    )
    consent = get_or_create_consent(db, token_student_id)
    assert consent is not None
    if not consent.job_action_tracking_enabled:
        raise StudentForbiddenError("job action tracking consent is disabled")
    return list(
        db.execute(
            select(JobAction)
            .where(
                JobAction.student_id == token_student_id,
                JobAction.source == "学生自报",
            )
            .order_by(JobAction.occurred_at.desc(), JobAction.id.desc())
        ).scalars()
    )
