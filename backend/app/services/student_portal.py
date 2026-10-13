"""T18 production student self-service facts and workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.core.crypto import decrypt_field
from app.models import (
    AcademicClass,
    AuditLog,
    College,
    Fair,
    FairCheckin,
    FairCompany,
    Job,
    JobAction,
    JobApplication,
    JobParse,
    Major,
    Match,
    Student,
    StudentConsent,
)
from app.models.enums import EmploymentStatus, JobActionType
from app.services.student_feedback import public_feedback, stored_feedback
from app.services.student_visibility import safe_recommendation_reason


class StudentPortalError(RuntimeError):
    pass


class StudentPortalNotFound(StudentPortalError):
    pass


class StudentPortalForbidden(StudentPortalError):
    pass


class StudentPortalConflict(StudentPortalError):
    pass


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
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 43))"),
        {"key": f"{namespace}:{key}"},
    )


def student_me(db: Session, *, student_id: int) -> dict:
    row = db.execute(
        select(Student, College, Major, AcademicClass)
        .join(College, College.id == Student.college_id)
        .join(Major, Major.id == Student.major_id)
        .outerjoin(AcademicClass, AcademicClass.id == Student.class_id)
        .where(Student.id == student_id)
    ).one_or_none()
    if row is None:
        raise StudentPortalNotFound("学生本人档案不存在")
    student, college, major, academic_class = row
    return {
        "student_no": student.student_no,
        "name": student.name,
        "college_name": college.name,
        "major_name": major.name,
        "class_name": academic_class.name if academic_class else None,
        "cohort_year": student.cohort_year,
        "degree_level": student.degree_level,
        "contact": decrypt_field(student.contact_encrypted),
        "employment_status": student.status,
    }


def read_student_consent(db: Session, *, student_id: int) -> dict[str, bool | int]:
    consent = db.execute(
        select(StudentConsent).where(StudentConsent.student_id == student_id)
    ).scalar_one_or_none()
    if consent is None:
        return {
            "recommendation_enabled": False,
            "growth_profile_enabled": False,
            "job_action_tracking_enabled": False,
            "revision": 0,
        }
    return {
        "recommendation_enabled": consent.recommendation_enabled,
        "growth_profile_enabled": consent.growth_profile_enabled,
        "job_action_tracking_enabled": consent.job_action_tracking_enabled,
        "revision": consent.revision,
    }


def write_student_consent(
    db: Session,
    *,
    student_id: int,
    values: dict[str, bool],
    expected_revision: int,
) -> dict[str, bool | int]:
    student = db.get(Student, student_id)
    if student is None:
        raise StudentPortalNotFound("学生本人档案不存在")
    _advisory_lock(db, "student-consent", str(student_id))
    consent = db.execute(
        select(StudentConsent)
        .where(StudentConsent.student_id == student_id)
        .with_for_update()
    ).scalar_one_or_none()
    current_revision = consent.revision if consent is not None else 0
    if current_revision != expected_revision:
        raise StudentPortalConflict(
            "数据使用设置已在其他窗口发生变化，请重新载入后核对"
        )
    before = read_student_consent(db, student_id=student_id)
    if consent is None:
        consent = StudentConsent(
            student_id=student_id,
            recommendation_enabled=False,
            growth_profile_enabled=False,
            job_action_tracking_enabled=False,
            created_by=f"student:{student_id}",
        )
        db.add(consent)
        db.flush()
    for key, value in values.items():
        setattr(consent, key, value)
    after = {
        "recommendation_enabled": consent.recommendation_enabled,
        "growth_profile_enabled": consent.growth_profile_enabled,
        "job_action_tracking_enabled": consent.job_action_tracking_enabled,
        "revision": consent.revision,
    }
    if before != after:
        if current_revision > 0:
            consent.revision += 1
            after["revision"] = consent.revision
        record_db(
            db,
            actor=f"student:{student_id}",
            actor_role="student",
            source_channel="student",
            action="student.consent.update",
            object_type="student_consent",
            object_id=consent.id,
            change_summary={"before": before, "after": after},
        )
    db.commit()
    return after


def _recommendation_rows(db: Session, student_id: int):
    return db.execute(
        select(Match, Job, JobParse, JobApplication)
        .join(Job, Job.id == Match.job_id)
        .outerjoin(JobParse, JobParse.job_id == Job.id)
        .outerjoin(
            JobApplication,
            (JobApplication.student_id == student_id)
            & (JobApplication.job_id == Job.id),
        )
        .where(Match.student_id == student_id, Match.pushed_at.is_not(None))
        .order_by(Match.pushed_at.desc(), Match.score.desc(), Match.id.desc())
    ).all()


def production_recommendations(db: Session, *, student_id: int) -> dict:
    consent = read_student_consent(db, student_id=student_id)
    if not consent["recommendation_enabled"]:
        return {"enabled": False, "items": []}
    items = []
    for match, job, parsed, application in _recommendation_rows(db, student_id):
        items.append(
            {
                "match_id": match.id,
                "job_id": job.id,
                "company": job.company,
                "title": job.title,
                "score": float(match.score),
                "reason_text": safe_recommendation_reason(match.reason_text),
                "feedback": public_feedback(match.feedback),
                "pushed_at": match.pushed_at,
                "location": parsed.location if parsed else None,
                "degree_req": parsed.degree_req if parsed else None,
                "salary_min": parsed.salary_min if parsed else None,
                "salary_max": parsed.salary_max if parsed else None,
                "deadline": parsed.deadline if parsed else None,
                "application_status": application.status if application else None,
            }
        )
    return {"enabled": True, "items": items}


def student_job_detail(db: Session, *, student_id: int, job_id: int) -> dict:
    row = db.execute(
        select(Job, JobParse, Match, JobApplication)
        .outerjoin(JobParse, JobParse.job_id == Job.id)
        .outerjoin(
            Match,
            (Match.job_id == Job.id)
            & (Match.student_id == student_id)
            & (Match.pushed_at.is_not(None)),
        )
        .outerjoin(
            JobApplication,
            (JobApplication.job_id == Job.id)
            & (JobApplication.student_id == student_id),
        )
        .where(Job.id == job_id)
    ).one_or_none()
    if row is None or (row[2] is None and row[3] is None):
        raise StudentPortalNotFound("岗位不存在或尚未向本人开放")
    job, parsed, match, application = row
    if application is None:
        consent = read_student_consent(db, student_id=student_id)
        if not consent["recommendation_enabled"]:
            raise StudentPortalForbidden("岗位推荐已关闭；如需查看未投递岗位，请先重新授权")
    return {
        "job_id": job.id,
        "match_id": match.id if match else None,
        "company": job.company,
        "title": job.title,
        "raw_text": job.raw_text,
        "received_at": job.received_at,
        "location": parsed.location if parsed else None,
        "degree_req": parsed.degree_req if parsed else None,
        "salary_min": parsed.salary_min if parsed else None,
        "salary_max": parsed.salary_max if parsed else None,
        "deadline": parsed.deadline if parsed else None,
        "score": float(match.score) if match else None,
        "reason_text": safe_recommendation_reason(match.reason_text) if match else None,
        "feedback": public_feedback(match.feedback) if match else None,
        "application_status": application.status if application else None,
        "application_id": application.id if application else None,
        "disclosure_authorized": (
            application.enterprise_disclosure_authorized_at is not None
            and application.enterprise_disclosure_revoked_at is None
            if application
            else None
        ),
        "application_revision": application.revision if application else None,
    }


def update_production_feedback(
    db: Session,
    *,
    student_id: int,
    match_id: int,
    feedback: str,
) -> Match:
    if feedback == "已投":
        raise StudentPortalConflict("请使用岗位投递操作生成可追溯的正式申请")
    # Serialize this write with consent changes.  If withdrawal committed first,
    # no later legacy/deep-link request may append a recommendation intention.
    _advisory_lock(db, "student-consent", str(student_id))
    consent = read_student_consent(db, student_id=student_id)
    if not consent["recommendation_enabled"]:
        raise StudentPortalForbidden("岗位推荐已关闭，不能继续保存岗位意向")
    match = db.execute(
        select(Match)
        .where(
            Match.id == match_id,
            Match.student_id == student_id,
            Match.pushed_at.is_not(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if match is None:
        raise StudentPortalNotFound("推荐岗位不存在或尚未向本人开放")
    application_id = db.execute(
        select(JobApplication.id).where(
            JobApplication.student_id == student_id,
            JobApplication.match_id == match.id,
        )
    ).scalar_one_or_none()
    if application_id is not None:
        raise StudentPortalConflict("该岗位已经正式投递，普通反馈不能覆盖投递事实")
    normalized = stored_feedback(feedback)
    before = public_feedback(match.feedback)
    if match.feedback == normalized and match.feedback_at is not None:
        db.commit()
        return match
    match.feedback = normalized
    match.feedback_at = datetime.now(UTC)
    record_db(
        db,
        actor=f"student:{student_id}",
        actor_role="student",
        source_channel="student",
        action="student.match.feedback",
        object_type="match",
        object_id=match.id,
        change_summary={"before": before, "after": feedback},
    )
    db.commit()
    db.refresh(match)
    return match


def _application_payload(
    application: JobApplication,
    job: Job,
    *,
    already_submitted: bool,
) -> dict:
    return {
        "application_id": application.id,
        "job_id": job.id,
        "match_id": application.match_id,
        "company": job.company,
        "title": job.title,
        "status": application.status,
        "submitted_at": application.submitted_at,
        "disclosure_authorized": application.enterprise_disclosure_authorized_at
        is not None
        and application.enterprise_disclosure_revoked_at is None,
        "already_submitted": already_submitted,
        "revision": application.revision,
    }


def _truncate_label(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1]}…"


def _job_action_target(company: str, title: str) -> str:
    """Build a useful label that always fits ``job_actions.target`` (128 chars)."""
    separator = " · "
    combined = f"{company}{separator}{title}"
    if len(combined) <= 128:
        return combined

    company_budget = min(len(company), 62)
    title_budget = 128 - len(separator) - company_budget
    if len(title) < title_budget:
        company_budget = min(len(company), 128 - len(separator) - len(title))
        title_budget = 128 - len(separator) - company_budget
    return (
        f"{_truncate_label(company, company_budget)}"
        f"{separator}{_truncate_label(title, title_budget)}"
    )


def create_student_application(
    db: Session,
    *,
    student_id: int,
    job_id: int,
    match_id: int | None,
    disclose_to_employer: bool,
    idempotency_key: UUID,
    fair_id: int | None = None,
) -> dict:
    key = str(idempotency_key)
    request_hash = _request_hash(
        {
            "student_id": student_id,
            "job_id": job_id,
            "match_id": match_id,
            "disclose_to_employer": disclose_to_employer,
            "fair_id": fair_id,
        }
    )
    _advisory_lock(db, "student.application.request", key)
    replay = db.execute(
        select(JobApplication, Job)
        .join(Job, Job.id == JobApplication.job_id)
        .where(JobApplication.idempotency_key == key)
    ).one_or_none()
    if replay is not None:
        application, job = replay
        if application.student_id != student_id or application.request_hash != request_hash:
            raise StudentPortalConflict("该操作标识已用于另一份岗位投递内容")
        db.commit()
        return _application_payload(application, job, already_submitted=True)

    _advisory_lock(db, "student-consent", str(student_id))
    consent = read_student_consent(db, student_id=student_id)
    if not consent["job_action_tracking_enabled"]:
        raise StudentPortalForbidden("请先在数据使用设置中允许记录求职进展")

    _advisory_lock(db, "student.application.job", f"{student_id}:{job_id}")
    # T16 rollback locks Student before inspecting dependent Match/Application
    # rows.  Take the same parent-row lock first so a concurrent rollback can
    # never hold Student while this transaction holds Match and waits for the
    # JobApplication/JobAction foreign-key check on Student.
    student = db.execute(
        select(Student)
        .where(Student.id == student_id)
        .with_for_update(read=True, key_share=True)
    ).scalar_one_or_none()
    if student is None:
        raise StudentPortalNotFound("学生本人档案不存在")

    # Management push uses Job -> Match after the shared Student parent lock.
    # KEY SHARE still allows concurrent readers and prevents the implicit
    # foreign-key checks after Match is held from reversing the lock order.
    job = db.execute(
        select(Job)
        .where(Job.id == job_id)
        .with_for_update(read=True, key_share=True)
    ).scalar_one_or_none()
    if job is None:
        raise StudentPortalNotFound("岗位不存在或尚未向本人开放")
    pushed = db.execute(
        select(Match)
        .where(
            Match.student_id == student_id,
            Match.job_id == job_id,
            Match.pushed_at.is_not(None),
            *([Match.id == match_id] if match_id is not None else []),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if pushed is None:
        raise StudentPortalNotFound("岗位不存在或尚未向本人开放")
    match = pushed
    fair_source: str | None = None
    if fair_id is not None:
        fair_company = db.execute(
            select(FairCompany)
            .where(
                FairCompany.fair_id == fair_id,
                FairCompany.job_id == job_id,
                FairCompany.status == "已通过",
            )
            .with_for_update(read=True)
        ).scalar_one_or_none()
        if fair_company is None:
            raise StudentPortalConflict("该岗位不属于所选招聘会的已审核参会岗位")
        fair_source = f"fair:{fair_id}"
    existing = db.execute(
        select(JobApplication)
        .where(
            JobApplication.student_id == student_id,
            JobApplication.job_id == job_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        if fair_source is not None:
            existing_source = db.execute(
                select(JobAction.source).where(
                    JobAction.application_id == existing.id
                )
            ).scalar_one_or_none()
            if existing_source != fair_source:
                raise StudentPortalConflict(
                    "该岗位已从其他入口投递，不能改写为招聘会投递"
                )
        existing_disclosure = (
            existing.enterprise_disclosure_authorized_at is not None
            and existing.enterprise_disclosure_revoked_at is None
        )
        if existing_disclosure != disclose_to_employer:
            raise StudentPortalConflict(
                "该岗位已经投递；企业资料授权如需变更，请在申请记录中单独处理"
            )
        db.commit()
        return _application_payload(existing, job, already_submitted=True)

    now = datetime.now(UTC)
    application = JobApplication(
        student_id=student_id,
        job_id=job_id,
        match_id=match.id,
        status="submitted",
        idempotency_key=key,
        request_hash=request_hash,
        submitted_at=now,
        status_updated_at=now,
        enterprise_disclosure_authorized_at=now if disclose_to_employer else None,
        revision=1,
        created_by=f"student:{student_id}",
    )
    db.add(application)
    db.flush()
    action = JobAction(
        student_id=student_id,
        type=JobActionType.APPLY.value,
        target=_job_action_target(job.company, job.title),
        occurred_at=now,
        source=fair_source or "学生正式投递",
        application_id=application.id,
        created_by=f"student:{student_id}",
    )
    db.add(action)
    match.feedback = stored_feedback("已投")
    match.feedback_at = now
    db.flush()
    record_db(
        db,
        actor=f"student:{student_id}",
        actor_role="student",
        source_channel="student",
        correlation_id=f"STU-APP-{application.id}",
        action="student.application.submit",
        object_type="job_application",
        object_id=application.id,
        change_summary={
            "student_id": student_id,
            "job_id": job_id,
            "match_id": match.id,
            "status": application.status,
            "enterprise_disclosure_authorized": disclose_to_employer,
            "job_action_id": action.id,
            "fair_id": fair_id,
        },
    )
    db.commit()
    db.refresh(application)
    return _application_payload(application, job, already_submitted=False)


def update_application_disclosure(
    db: Session,
    *,
    student_id: int,
    application_id: int,
    authorized: bool,
    revision: int,
    idempotency_key: UUID,
) -> dict:
    """Change enterprise disclosure independently from the application fact."""
    key = str(idempotency_key)
    correlation_id = f"STU-DISC-{key}"
    _advisory_lock(db, "student.application.disclosure", key)

    replay = db.execute(
        select(AuditLog).where(AuditLog.correlation_id == correlation_id)
    ).scalar_one_or_none()
    if replay is not None:
        summary = replay.change_summary or {}
        if (
            summary.get("student_id") != student_id
            or summary.get("application_id") != application_id
            or summary.get("authorized") is not authorized
            or summary.get("requested_revision") != revision
        ):
            raise StudentPortalConflict("该操作标识已用于另一份资料披露变更")
        row = db.execute(
            select(JobApplication, Job)
            .join(Job, Job.id == JobApplication.job_id)
            .where(
                JobApplication.id == application_id,
                JobApplication.student_id == student_id,
            )
        ).one_or_none()
        if row is None:
            raise StudentPortalNotFound("申请记录不存在")
        application, job = row
        current = (
            application.enterprise_disclosure_authorized_at is not None
            and application.enterprise_disclosure_revoked_at is None
        )
        if (
            current is not authorized
            or application.revision != summary.get("resulting_revision")
        ):
            raise StudentPortalConflict("该申请在原操作后已经发生变化，请重新载入")
        db.commit()
        return _application_payload(application, job, already_submitted=True)

    row = db.execute(
        select(JobApplication, Job)
        .join(Job, Job.id == JobApplication.job_id)
        .where(
            JobApplication.id == application_id,
            JobApplication.student_id == student_id,
        )
        .with_for_update(of=JobApplication)
    ).one_or_none()
    if row is None:
        raise StudentPortalNotFound("申请记录不存在")
    application, job = row
    if application.revision != revision:
        raise StudentPortalConflict("申请记录已更新，请重新载入后再修改披露设置")

    before = (
        application.enterprise_disclosure_authorized_at is not None
        and application.enterprise_disclosure_revoked_at is None
    )
    now = datetime.now(UTC)
    if before != authorized:
        if authorized:
            application.enterprise_disclosure_authorized_at = now
            application.enterprise_disclosure_revoked_at = None
        else:
            application.enterprise_disclosure_revoked_at = now
        application.revision += 1

    record_db(
        db,
        actor=f"student:{student_id}",
        actor_role="student",
        source_channel="student",
        correlation_id=correlation_id,
        action="student.application.disclosure.update",
        object_type="job_application",
        object_id=application.id,
        change_summary={
            "student_id": student_id,
            "application_id": application.id,
            "authorized": authorized,
            "before": before,
            "requested_revision": revision,
            "resulting_revision": application.revision,
        },
    )
    db.commit()
    db.refresh(application)
    return _application_payload(application, job, already_submitted=False)


def list_student_applications(db: Session, *, student_id: int) -> list[dict]:
    rows = db.execute(
        select(JobApplication, Job)
        .join(Job, Job.id == JobApplication.job_id)
        .where(JobApplication.student_id == student_id)
        .order_by(JobApplication.submitted_at.desc(), JobApplication.id.desc())
    ).all()
    return [
        _application_payload(application, job, already_submitted=False)
        for application, job in rows
    ]


def student_progress(db: Session, *, student_id: int) -> dict:
    student = db.get(Student, student_id)
    if student is None:
        raise StudentPortalNotFound("学生本人档案不存在")
    application_rows = db.execute(
        select(JobApplication, Job)
        .join(Job, Job.id == JobApplication.job_id)
        .where(JobApplication.student_id == student_id)
        .order_by(JobApplication.submitted_at.desc())
    ).all()
    fair_rows = db.execute(
        select(FairCheckin, Fair)
        .join(Fair, Fair.id == FairCheckin.fair_id)
        .where(FairCheckin.student_id == student_id)
        .order_by(FairCheckin.at.desc())
    ).all()
    feedback_rows = db.execute(
        select(Match, Job)
        .join(Job, Job.id == Match.job_id)
        .where(
            Match.student_id == student_id,
            Match.feedback_at.is_not(None),
            Match.feedback != stored_feedback("已投"),
        )
        .order_by(Match.feedback_at.desc())
    ).all()
    consent = read_student_consent(db, student_id=student_id)

    events: list[dict] = []
    for application, job in application_rows:
        events.append(
            {
                "event_id": f"application:{application.id}",
                "kind": "application",
                "title": f"投递 {job.title}",
                "detail": job.company,
                "status": application.status,
                "occurred_at": application.submitted_at,
            }
        )
    for checkin, fair in fair_rows:
        events.append(
            {
                "event_id": f"fair:{checkin.id}",
                "kind": "fair",
                "title": f"参加 {fair.name}",
                "detail": fair.location or "地点待定",
                "status": "已签到",
                "occurred_at": checkin.at,
            }
        )
    for match, job in feedback_rows:
        label = public_feedback(match.feedback)
        events.append(
            {
                "event_id": f"feedback:{match.id}",
                "kind": "feedback",
                "title": f"反馈 {job.title}",
                "detail": job.company,
                "status": label or "已反馈",
                "occurred_at": match.feedback_at,
            }
        )
    if student.status != EmploymentStatus.UNSETTLED.value and student.status_occurred_at:
        events.append(
            {
                "event_id": f"outcome:{student.id}",
                "kind": "outcome",
                "title": "就业去向已登记",
                "detail": "请核对本人档案中的去向事实",
                "status": student.status,
                "occurred_at": student.status_occurred_at,
            }
        )
    events.sort(key=lambda item: (item["occurred_at"], item["event_id"]), reverse=True)

    if student.status != EmploymentStatus.UNSETTLED.value:
        stage = "去向已落实"
        next_action = {
            "title": "核对本人就业档案",
            "description": "确认去向状态与发生时间是否准确，如有问题联系学院辅导员。",
            "target_path": "/student/pages/profile/index",
        }
    elif not consent["recommendation_enabled"]:
        stage = "完善数据设置"
        next_action = {
            "title": "核对岗位推荐设置",
            "description": "岗位推荐当前未开启；请先在“我的—数据使用设置”中确认本人选择。",
            "target_path": "/student/pages/profile/index",
        }
    elif not consent["job_action_tracking_enabled"]:
        stage = "准备求职记录"
        next_action = {
            "title": "核对求职记录设置",
            "description": "提交岗位前，请先在“我的—数据使用设置”中确认是否保存本人求职进展。",
            "target_path": "/student/pages/profile/index",
        }
    elif not application_rows:
        stage = "岗位探索"
        next_action = {
            "title": "查看一份适合岗位",
            "description": "从校方已推送岗位开始，阅读岗位详情后再决定是否投递。",
            "target_path": "/student/pages/jobs/index",
        }
    elif not fair_rows:
        stage = "投递推进"
        next_action = {
            "title": "参加一场招聘活动",
            "description": "查看近期招聘会，使用现场场次码完成本人签到。",
            "target_path": "/student/pages/fairs/index",
        }
    else:
        stage = "持续跟进"
        next_action = {
            "title": "检查最新申请进展",
            "description": "查看申请与活动记录，及时处理新的反馈。",
            "target_path": "/student/pages/jobs/index?tab=applications",
        }
    return {
        "stage": stage,
        "employment_status": student.status,
        "application_count": len(application_rows),
        "fair_checkin_count": len(fair_rows),
        "next_action": next_action,
        "events": events,
    }
