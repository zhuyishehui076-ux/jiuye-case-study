"""Provision one coherent D2 demo story without persisting plaintext credentials."""

from __future__ import annotations

import argparse
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_db
from app.core.config import require_demo_profile, settings
from app.core.security import hash_password
from app.d2.student_service import binding_code_digest
from app.db.session import SessionLocal
from app.models import (
    Assessment,
    AssistCase,
    AssistRecord,
    EnterpriseAccessToken,
    EnterpriseJobSubmission,
    Fair,
    FairCheckin,
    FairCompany,
    Job,
    JobParse,
    Match,
    Profile,
    Student,
    StudentBinding,
    StudentConsent,
)
from app.models.enums import AssistActionType, AssistStatus, JobSource, MatchFeedback, RiskLevel
from app.seed.demo import DEMO_AS_OF_DATE
from app.services.assist_idempotency import (
    assist_record_request_hash,
    deterministic_assist_idempotency_key,
)
from app.services.installation import ensure_installation_profile
from app.services.jobs import calculate_matches_for_job
from app.services.profiles import rebuild_profile_for_student

DEMO_ACTOR = "d2-seed"
DEMO_COMPANY = "航图数据科技（模拟企业）"
DEMO_JOB_TITLE = "就业数据产品助理"
DEMO_JOB_TEXT = (
    "招聘数据产品助理 5 名，本科及以上学历，计算机科学与技术、软件工程、"
    "数据科学相关专业；熟悉数据分析与产品需求梳理，工作地点武汉。"
)


def _target_student(db: Session) -> Student:
    student = db.execute(
        select(Student)
        .where(Student.name == "模拟故事学生01")
        .order_by(Student.id)
    ).scalars().first()
    if student is None:
        student = db.execute(
            select(Student)
            .where(Student.risk_level == RiskLevel.RED.value)
            .order_by(Student.id)
        ).scalars().first()
    if student is None:
        raise RuntimeError("D2 seed requires the fixed story student or one red-risk demo student")
    return student


def _ensure_profile(db: Session, student: Student) -> Profile:
    existing = db.execute(
        select(Profile)
        .where(Profile.student_id == student.id)
        .order_by(Profile.term.desc(), Profile.id.desc())
    ).scalars().first()
    if existing is not None:
        return existing
    term = db.execute(
        select(Assessment.term)
        .where(Assessment.student_id == student.id)
        .order_by(Assessment.id.desc())
    ).scalars().first() or "2024-2025-1"
    return rebuild_profile_for_student(
        db,
        student.id,
        term=term,
        actor=DEMO_ACTOR,
        provider=None,
    )


def _ensure_student_binding_shell(db: Session, *, student: Student) -> StudentBinding:
    binding = db.execute(
        select(StudentBinding).where(StudentBinding.student_id == student.id)
    ).scalar_one_or_none()
    if binding is None:
        discarded = secrets.token_urlsafe(32)
        binding = StudentBinding(
            student_id=student.id,
            binding_code_digest=binding_code_digest(discarded),
            binding_code_hash=hash_password(discarded),
            is_active=True,
            created_by=DEMO_ACTOR,
        )
        db.add(binding)
        del discarded
    consent = db.execute(
        select(StudentConsent).where(StudentConsent.student_id == student.id)
    ).scalar_one_or_none()
    if consent is None:
        consent = StudentConsent(student_id=student.id, created_by=DEMO_ACTOR)
        db.add(consent)
    consent.recommendation_enabled = True
    consent.growth_profile_enabled = True
    consent.job_action_tracking_enabled = True
    db.flush()
    return binding


def _ensure_demo_assist_record(db: Session, student: Student) -> AssistRecord:
    case = db.execute(
        select(AssistCase).where(AssistCase.student_id == student.id)
    ).scalar_one_or_none()
    if case is None:
        case = AssistCase(
            student_id=student.id,
            opened_at=DEMO_AS_OF_DATE,
            opened_reason=student.risk_reason or "近期求职行动较少，需要跟进",
            status=AssistStatus.OPEN.value,
            created_by=DEMO_ACTOR,
        )
        db.add(case)
        db.flush()
    record = db.execute(
        select(AssistRecord).where(
            AssistRecord.case_id == case.id,
            AssistRecord.created_by == DEMO_ACTOR,
        )
    ).scalars().first()
    if record is None:
        action_type = AssistActionType.INTERVIEW.value
        note = "已确认求职方向，下一步推荐武汉地区的数据产品岗位。"
        actor = "就业办老师"
        record = AssistRecord(
            case_id=case.id,
            action_type=action_type,
            note=note,
            actor=actor,
            occurred_at=DEMO_AS_OF_DATE - timedelta(days=1),
            idempotency_key=deterministic_assist_idempotency_key(
                f"d2-seed:{student.student_no}"
            ),
            request_hash=assist_record_request_hash(
                case_id=case.id,
                action_type=action_type,
                note=note,
                actor=actor,
            ),
            created_by=DEMO_ACTOR,
        )
        db.add(record)
        db.flush()
    return record


def _ensure_demo_job(db: Session) -> tuple[Job, JobParse]:
    job = db.execute(
        select(Job)
        .where(Job.company == DEMO_COMPANY, Job.title == DEMO_JOB_TITLE)
        .order_by(Job.id)
    ).scalars().first()
    if job is None:
        job = Job(
            company=DEMO_COMPANY,
            title=DEMO_JOB_TITLE,
            raw_text=DEMO_JOB_TEXT,
            source=JobSource.FORM.value,
            received_at=DEMO_AS_OF_DATE - timedelta(days=3),
            created_by=DEMO_ACTOR,
        )
        db.add(job)
        db.flush()
    else:
        job.company = DEMO_COMPANY
        job.raw_text = DEMO_JOB_TEXT

    parsed = db.execute(
        select(JobParse).where(JobParse.job_id == job.id)
    ).scalar_one_or_none()
    if parsed is None:
        parsed = JobParse(job_id=job.id, created_by=DEMO_ACTOR)
        db.add(parsed)
    parsed.degree_req = "本科"
    parsed.major_codes = ["080901", "080902", "080910"]
    parsed.direction_keywords = ["数据分析", "产品需求", "就业服务"]
    parsed.location = "武汉"
    parsed.salary_min = 8000
    parsed.salary_max = 12000
    parsed.headcount = 5
    parsed.parse_confidence = 0.95
    parsed.llm_meta = {
        "provider": "precomputed",
        "prompt_ver": "d2-demo-seed-v1",
    }
    parsed.needs_review = False
    parsed.review_reason = None
    db.flush()
    return job, parsed


def _ensure_demo_fair_company(
    db: Session,
    *,
    job: Job,
    student: Student,
) -> FairCompany | None:
    fair = db.execute(
        select(Fair)
        .where(Fair.status == "签到进行中")
        .order_by(Fair.held_at.desc().nullslast(), Fair.id.desc())
    ).scalars().first()
    if fair is None:
        return None
    row = db.execute(
        select(FairCompany).where(
            FairCompany.fair_id == fair.id,
            FairCompany.company == DEMO_COMPANY,
        )
    ).scalar_one_or_none()
    if row is None:
        row = FairCompany(
            fair_id=fair.id,
            company=DEMO_COMPANY,
            booth="A01",
            status="已通过",
            title=DEMO_JOB_TITLE,
            raw_text=DEMO_JOB_TEXT,
            job_id=job.id,
            created_by=DEMO_ACTOR,
        )
        db.add(row)
    else:
        row.status = "已通过"
        row.booth = row.booth or "A01"
        row.job_id = row.job_id or job.id
    # D2 的固定主故事必须能由学生本人完成一次真实签到。校园规模种子可能
    # 已随机抽中该学生，因此在重建演示凭证时只清理这一个内置模拟事实。
    existing_student_checkin = db.execute(
        select(FairCheckin).where(
            FairCheckin.fair_id == fair.id,
            FairCheckin.student_id == student.id,
        )
    ).scalar_one_or_none()
    if existing_student_checkin is not None:
        db.delete(existing_student_checkin)
    db.flush()
    return row


@dataclass(frozen=True)
class D2Story:
    student: Student
    profile: Profile
    job: Job
    parsed: JobParse
    fair_company: FairCompany | None


def ensure_d2_demo_story(db: Session) -> D2Story:
    """Build the coherent Demo story without changing any active credential."""
    require_demo_profile("provision_d2_demo")
    ensure_installation_profile(
        db,
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )
    student = _target_student(db)
    profile = _ensure_profile(db, student)
    _ensure_student_binding_shell(db, student=student)
    _ensure_demo_assist_record(db, student)
    job, parsed = _ensure_demo_job(db)
    existing_matches = db.execute(
        select(Match).where(Match.job_id == job.id)
    ).scalars().all()
    if len(existing_matches) < 10:
        existing_facts = {
            row.student_id: (row.pushed_at, row.feedback, row.created_by)
            for row in existing_matches
        }
        calculate_matches_for_job(db, job.id, actor=DEMO_ACTOR)
        refreshed_matches = db.execute(
            select(Match).where(Match.job_id == job.id)
        ).scalars().all()
        for row in refreshed_matches:
            if row.student_id in existing_facts:
                row.pushed_at, row.feedback, row.created_by = existing_facts[row.student_id]
    match = db.execute(
        select(Match).where(Match.job_id == job.id, Match.student_id == student.id)
    ).scalar_one_or_none()
    if match is None:
        calculate_matches_for_job(db, job.id, actor=DEMO_ACTOR)
        match = db.execute(
            select(Match).where(Match.job_id == job.id, Match.student_id == student.id)
        ).scalar_one()
    match.pushed_at = DEMO_AS_OF_DATE - timedelta(days=1)
    match.feedback = MatchFeedback.READ.value
    match.feedback_at = DEMO_AS_OF_DATE
    fair_company = _ensure_demo_fair_company(db, job=job, student=student)
    db.flush()
    return D2Story(
        student=student,
        profile=profile,
        job=job,
        parsed=parsed,
        fair_company=fair_company,
    )


def _ensure_demo_submission(
    db: Session, *, job: Job, access: EnterpriseAccessToken
) -> EnterpriseJobSubmission:
    submission = db.execute(
        select(EnterpriseJobSubmission).where(EnterpriseJobSubmission.job_id == job.id)
    ).scalar_one_or_none()
    if submission is None:
        submission = EnterpriseJobSubmission(
            access_token_id=access.id,
            job_id=job.id,
            created_by=DEMO_ACTOR,
        )
        db.add(submission)
    submission.access_token_id = access.id
    submission.status = "completed"
    submission.started_at = submission.started_at or DEMO_AS_OF_DATE - timedelta(days=2)
    submission.completed_at = DEMO_AS_OF_DATE
    submission.failure_detail = None
    db.flush()
    return submission


def provision_d2_demo(
    db: Session,
) -> dict[str, object]:
    """Provision only non-secret story data; credentials use the release journal."""
    require_demo_profile("provision_d2_demo")
    ensure_installation_profile(
        db,
        expected_profile=settings.profile,
        expected_installation_id=settings.installation_id,
    )
    story = ensure_d2_demo_story(db)
    record_db(
        db,
        actor=DEMO_ACTOR,
        action="d2_demo.provision",
        object_type="student",
        object_id=story.student.id,
        change_summary={
            "job_id": story.job.id,
            "profile_id": story.profile.id,
            "parse_id": story.parsed.id,
            "fair_company_id": story.fair_company.id if story.fair_company else None,
        },
    )
    db.commit()
    return {
        "student_id": story.student.id,
        "student_no": story.student.student_no,
        "enterprise_company": DEMO_COMPANY,
        "job_id": story.job.id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.d2.seed_demo")
    parser.parse_args(argv)
    with SessionLocal() as db:
        story = ensure_d2_demo_story(db)
        result = {"student_id": story.student.id, "job_id": story.job.id}
        db.commit()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
