"""T5 M3 学生画像 API。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.core.audit import record_db
from app.core.config import settings
from app.core.rbac import Role
from app.db.session import get_db
from app.llm.provider import get_provider
from app.models import AssistCase, AssistRecord, College, JobAction, Major, Profile, Student
from app.models.enums import AssistStatus, EmploymentStatus, JobActionType
from app.schemas.profiles import (
    ProfileDetailResponse,
    ProfileListItem,
    ProfileRebuildResponse,
    StudentOutcomeResponse,
    StudentOutcomeUpdate,
)
from app.services.dashboard import refresh_dashboard_views
from app.services.llm_batches import (
    TASK_PROFILE_NARRATIVE,
    enqueue_llm_batch,
)
from app.services.operation_receipts import (
    OperationReceiptConflict,
    claim_operation,
    finish_operation,
)
from app.services.profiles import build_student_fact_chain, derive_journey_stage, rebuild_profiles
from app.services.risk import RiskInput, evaluate_risk
from app.services.student_feedback import match_feedback_by_student
from app.services.thresholds import load_risk_thresholds
from app.services.write_idempotency import InvalidWriteOperationKey

router = APIRouter(prefix="/profiles", tags=["profiles"])

READ_ROLES = (
    Role.EMPLOYMENT_OFFICE,
    Role.COUNSELOR,
)
WRITE_ROLES = (Role.EMPLOYMENT_OFFICE,)
OUTCOME_WRITE_ROLES = (Role.EMPLOYMENT_OFFICE, Role.COUNSELOR)


@router.put(
    "/students/{student_id}/outcome",
    response_model=StudentOutcomeResponse,
    dependencies=[Depends(require_roles(*OUTCOME_WRITE_ROLES))],
)
def update_student_outcome(
    student_id: int,
    payload: StudentOutcomeUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StudentOutcomeResponse:
    # Route-level require_roles runs before the handler.  Once authorized,
    # serialize confirmations for the same student so a concurrent identical
    # request observes the committed fact and takes the idempotent fast path
    # instead of writing a second audit event.
    student = _lock_student_in_scope(db, student_id=student_id, user=user)
    if (
        payload.status == student.status
        and payload.occurred_at is None
        and student.status_occurred_at is not None
    ):
        return StudentOutcomeResponse(
            student_id=student.id,
            status=student.status,
            occurred_at=student.status_occurred_at,
        )

    before = {
        "status": student.status,
        "occurred_at": student.status_occurred_at.isoformat()
        if student.status_occurred_at
        else None,
    }
    occurred_at = payload.occurred_at or datetime.now(UTC)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    else:
        occurred_at = occurred_at.astimezone(UTC)

    settled_statuses = {
        EmploymentStatus.SIGNED.value,
        EmploymentStatus.FURTHER_STUDY.value,
        EmploymentStatus.ABROAD.value,
        EmploymentStatus.MILITARY.value,
        EmploymentStatus.FLEXIBLE.value,
    }
    assist_case_status = (
        AssistStatus.CLOSED_EMPLOYED.value
        if payload.status in settled_statuses
        else AssistStatus.OPEN.value
    )
    signing_action: JobAction | None = None
    try:
        student.status = payload.status
        student.status_occurred_at = occurred_at
        if payload.status == EmploymentStatus.SIGNED.value:
            accepted_action = db.execute(
                select(JobAction)
                .where(
                    JobAction.student_id == student.id,
                    JobAction.type == JobActionType.ACCEPTED.value,
                )
                .order_by(JobAction.occurred_at.desc(), JobAction.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if accepted_action is not None:
                existing_signing = db.execute(
                    select(JobAction.id)
                    .where(
                        JobAction.student_id == student.id,
                        JobAction.type == JobActionType.SIGN.value,
                        JobAction.source == accepted_action.source,
                        JobAction.target == accepted_action.target,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if existing_signing is None:
                    signing_action = JobAction(
                        student_id=student.id,
                        type=JobActionType.SIGN.value,
                        target=accepted_action.target,
                        occurred_at=occurred_at,
                        source=accepted_action.source,
                        created_by=user.username,
                    )
                    db.add(signing_action)
                    db.flush()
        assist_cases = db.execute(
            select(AssistCase).where(AssistCase.student_id == student.id)
        ).scalars().all()
        last_action_at = db.scalar(
            select(func.max(JobAction.occurred_at)).where(JobAction.student_id == student.id)
        )
        apply_count = int(
            db.scalar(
                select(func.count())
                .select_from(JobAction)
                .where(
                    JobAction.student_id == student.id,
                    JobAction.type == JobActionType.APPLY.value,
                )
            )
            or 0
        )
        college_student_count = int(
            db.scalar(
                select(func.count())
                .select_from(Student)
                .where(Student.college_id == student.college_id)
            )
            or 0
        )
        college_apply_count = int(
            db.scalar(
                select(func.count())
                .select_from(JobAction)
                .join(Student, Student.id == JobAction.student_id)
                .where(
                    Student.college_id == student.college_id,
                    JobAction.type == JobActionType.APPLY.value,
                )
            )
            or 0
        )
        weeks_since_last_action = (
            None
            if last_action_at is None
            else max((occurred_at - last_action_at).days // 7, 0)
        )
        risk_result = evaluate_risk(
            RiskInput(
                status=student.status,
                is_graduation_year=student.cohort_year == occurred_at.year,
                current_month=occurred_at.month,
                weeks_since_last_job_action=weeks_since_last_action,
                apply_count=apply_count,
                college_avg_apply=(college_apply_count / college_student_count)
                if college_student_count
                else 0.0,
            ),
            load_risk_thresholds(db),
        )
        student.risk_level = risk_result.level.value
        student.risk_reason = "；".join(risk_result.reasons) or None
        for case in assist_cases:
            case.status = assist_case_status
        record_db(
            db,
            actor=user.username,
            actor_id=user.user_id,
            actor_role=user.role.value,
            source_channel="management_api",
            correlation_id=(
                f"STUDENT-{student.id}-OUTCOME-"
                f"{occurred_at.strftime('%Y%m%dT%H%M%S%fZ')}"
            ),
            action="student.outcome.confirm",
            object_type="student",
            object_id=student.id,
            change_summary={
                "before": before,
                "after": {
                    "status": payload.status,
                    "occurred_at": occurred_at.isoformat(),
                    "risk_level": student.risk_level,
                    "risk_reason": student.risk_reason,
                    "assist_case_status": assist_case_status,
                    "signing_action_id": (
                        signing_action.id if signing_action is not None else None
                    ),
                    "signing_action_source": (
                        signing_action.source if signing_action is not None else None
                    ),
                },
            },
        )
        # 先把 ORM 变更送入当前事务，再刷新 MV；任一失败都由同一 rollback 撤销。
        db.flush()
        refresh_dashboard_views(db, concurrently=False, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return StudentOutcomeResponse(
        student_id=student.id,
        status=student.status,
        occurred_at=occurred_at,
    )


def _scope_college(user: CurrentUser) -> int | None:
    return user.college_id if user.role is Role.COUNSELOR else None


def _lock_student_in_scope(
    db: Session,
    *,
    student_id: int,
    user: CurrentUser,
) -> Student:
    """Lock only an authorized student row before an outcome write.

    A counselor must not acquire a write lock on another college's student and
    only then discover the scope violation.  The secondary unlocked lookup is
    used solely to preserve the API's 404-vs-403 contract.
    """
    statement = select(Student).where(Student.id == student_id)
    if user.role is Role.COUNSELOR:
        if user.college_id is None:
            raise HTTPException(
                status_code=403,
                detail="当前辅导员账号未绑定学院，无法访问学院业务数据。",
            )
        student = db.execute(
            statement
            .where(Student.college_id == user.college_id)
            .with_for_update()
        ).scalar_one_or_none()
        if student is not None:
            return student
        exists = db.scalar(select(Student.id).where(Student.id == student_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="student not found")
        raise HTTPException(status_code=403, detail="辅导员仅可查看本学院画像")

    student = db.execute(statement.with_for_update()).scalar_one_or_none()
    return _assert_student_scope(student, user)


def _assert_student_scope(student: Student | None, user: CurrentUser) -> Student:
    if student is None:
        raise HTTPException(status_code=404, detail="student not found")
    if user.role is Role.COUNSELOR and student.college_id != user.college_id:
        raise HTTPException(status_code=403, detail="辅导员仅可查看本学院画像")
    return student


def _score_avg(profile: Profile | None) -> float | None:
    if profile is None:
        return None
    values = [float(v) for v in profile.six_dims_json.values()]
    return round(sum(values) / len(values), 1) if values else None


@router.post(
    "/rebuild",
    response_model=ProfileRebuildResponse,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
def rebuild_profiles_endpoint(
    term: str = Query(min_length=1, max_length=16),
    narrative_mode: Literal["queue", "sync"] = "queue",
    student_id: int | None = Query(default=None, ge=1),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileRebuildResponse:
    try:
        receipt, replayed = claim_operation(
            db,
            namespace="profile.rebuild",
            idempotency_key=idempotency_key,
            actor=user.username,
            payload={
                "term": term,
                "narrative_mode": narrative_mode,
                "student_id": student_id,
            },
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if replayed:
        return ProfileRebuildResponse.model_validate(receipt.result_json)

    target_ids = (
        [student_id]
        if student_id is not None
        else list(db.execute(select(Student.id).order_by(Student.id)).scalars())
    )
    if student_id is not None and db.get(Student, student_id) is None:
        raise HTTPException(status_code=404, detail="student not found")

    if settings.llm_batch_enabled and not target_ids:
        result = ProfileRebuildResponse(
            term=term,
            rebuilt=0,
            narrative_mode="queue",
            status="completed",
            task_id=None,
        )
        finish_operation(receipt, result=result.model_dump(mode="json"))
        db.commit()
        return result

    if settings.llm_batch_enabled:
        rebuilt = rebuild_profiles(
            db,
            term=term,
            actor=user.username,
            provider=None,
            student_ids=target_ids,
        )
        task = enqueue_llm_batch(
            db,
            task_type=TASK_PROFILE_NARRATIVE,
            target_ids=target_ids,
            actor=user.username,
            term=term,
            idempotency_key=idempotency_key,
            commit=False,
            receipt_protected=True,
        )
        result = ProfileRebuildResponse(
            term=term,
            rebuilt=rebuilt,
            narrative_mode="queue",
            status="queued",
            task_id=task.id,
        )
    else:
        provider = get_provider() if narrative_mode == "sync" else None
        rebuilt = rebuild_profiles(
            db,
            term=term,
            actor=user.username,
            provider=provider,
            student_ids=target_ids,
        )
        result = ProfileRebuildResponse(
            term=term,
            rebuilt=rebuilt,
            narrative_mode=narrative_mode,
        )
    finish_operation(receipt, result=result.model_dump(mode="json"))
    db.commit()
    return result


@router.get(
    "/students",
    response_model=list[ProfileListItem],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_profile_students(
    keyword: str | None = Query(default=None, max_length=64),
    college_id: int | None = Query(default=None, ge=1),
    major_id: int | None = Query(default=None, ge=1),
    cohort_year: int | None = Query(default=None, ge=2000, le=2100),
    risk_level: Literal["red", "yellow", "green"] | None = None,
    attention_only: bool = False,
    employment_status: Literal[
        "未落实", "签约", "升学", "出国", "入伍", "灵活就业", "暂不就业"
    ]
    | None = None,
    journey_stage: Literal["发现", "帮扶", "推岗", "反馈", "结果"] | None = None,
    workflow_status: Literal[
        "未帮扶", "已帮扶", "已推岗", "待反馈", "已反馈", "待确认结果"
    ]
    | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProfileListItem]:
    latest = (
        select(Profile.student_id, func.max(Profile.term).label("term"))
        .group_by(Profile.student_id)
        .subquery()
    )
    stmt = (
        select(Student, College, Major, Profile)
        .join(College, Student.college_id == College.id)
        .join(Major, Student.major_id == Major.id)
        .outerjoin(latest, latest.c.student_id == Student.id)
        .outerjoin(
            Profile,
            (Profile.student_id == Student.id) & (Profile.term == latest.c.term),
        )
        .order_by(Student.id)
    )
    scoped_college_id = _scope_college(user)
    if scoped_college_id is not None:
        if college_id is not None and college_id != scoped_college_id:
            raise HTTPException(status_code=403, detail="辅导员仅可筛选本学院学生")
        stmt = stmt.where(Student.college_id == scoped_college_id)
    elif college_id is not None:
        stmt = stmt.where(Student.college_id == college_id)
    if major_id is not None:
        stmt = stmt.where(Student.major_id == major_id)
    if cohort_year is not None:
        stmt = stmt.where(Student.cohort_year == cohort_year)
    if risk_level is not None:
        stmt = stmt.where(Student.risk_level == risk_level)
    if attention_only:
        stmt = stmt.where(
            Student.status == EmploymentStatus.UNSETTLED.value,
            Student.risk_level.in_(["red", "yellow"]),
        )
    if employment_status is not None:
        stmt = stmt.where(Student.status == employment_status)
    if keyword:
        normalized_keyword = keyword.strip()
        if normalized_keyword:
            pattern = f"%{normalized_keyword}%"
            stmt = stmt.where(
                Student.student_no.ilike(pattern)
                | Student.name.ilike(pattern)
                | Major.name.ilike(pattern)
            )

    rows = db.execute(stmt).all()
    student_ids = [student.id for student, _, _, _ in rows]
    assist_facts = {
        student_id: (int(count), latest_at)
        for student_id, count, latest_at in db.execute(
            select(
                AssistCase.student_id,
                func.count(AssistRecord.id),
                func.max(func.coalesce(AssistRecord.occurred_at, AssistCase.opened_at)),
            )
            .outerjoin(AssistRecord, AssistRecord.case_id == AssistCase.id)
            .where(AssistCase.student_id.in_(student_ids))
            .group_by(AssistCase.student_id)
        ).all()
    } if student_ids else {}
    job_action_latest = dict(
        db.execute(
            select(JobAction.student_id, func.max(JobAction.occurred_at))
            .where(JobAction.student_id.in_(student_ids))
            .group_by(JobAction.student_id)
        ).all()
    ) if student_ids else {}
    pushed_facts = match_feedback_by_student(db, student_ids)
    result: list[ProfileListItem] = []
    for student, college, major, profile in rows:
        pushed_matches = pushed_facts.get(student.id, [])
        assist_count, latest_assist_at = assist_facts.get(student.id, (0, None))
        feedback_count = sum(item.get("feedback") is not None for item in pushed_matches)
        stage, _ = derive_journey_stage(
            student.status,
            assist_count,
            pushed_matches,
        )
        if journey_stage is not None and stage != journey_stage:
            continue
        workflow_statuses: list[
            Literal["未帮扶", "已帮扶", "已推岗", "待反馈", "已反馈", "待确认结果"]
        ] = []
        if assist_count == 0:
            workflow_statuses.append("未帮扶")
        else:
            workflow_statuses.append("已帮扶")
        if pushed_matches:
            workflow_statuses.append("已推岗")
        if len(pushed_matches) > feedback_count:
            workflow_statuses.append("待反馈")
        if feedback_count > 0:
            workflow_statuses.append("已反馈")
        if feedback_count > 0 and student.status == EmploymentStatus.UNSETTLED.value:
            workflow_statuses.append("待确认结果")
        if workflow_status is not None and workflow_status not in workflow_statuses:
            continue

        fact_times = [
            value
            for value in (
                student.status_occurred_at,
                latest_assist_at,
                job_action_latest.get(student.id),
                *(
                    item.get("feedback_at") or item.get("pushed_at")
                    for item in pushed_matches
                ),
            )
            if value is not None
        ]
        latest_fact_at = max(fact_times) if fact_times else None
        result.append(ProfileListItem(
            student_id=student.id,
            student_no=student.student_no,
            name=student.name,
            college_id=student.college_id,
            college_name=college.name,
            major_id=student.major_id,
            major_name=major.name,
            cohort_year=student.cohort_year,
            risk_level=student.risk_level,
            status=student.status,
            latest_term=profile.term if profile else None,
            score_avg=_score_avg(profile),
            narrative_status=str(profile.llm_meta.get("status", "missing"))
            if profile
            else "missing",
            journey_stage=stage,
            assist_record_count=assist_count,
            pushed_count=len(pushed_matches),
            feedback_count=feedback_count,
            workflow_statuses=workflow_statuses,
            latest_fact_at=latest_fact_at,
        ))

    risk_order = {"red": 0, "yellow": 1, "green": 2}

    def queue_order(item: ProfileListItem) -> int:
        statuses = set(item.workflow_statuses)
        if "待确认结果" in statuses:
            return 0
        if "待反馈" in statuses:
            return 1
        if "未帮扶" in statuses:
            return 2
        if "已帮扶" in statuses and "已推岗" not in statuses:
            return 3
        return 4

    result.sort(
        key=lambda item: (
            risk_order.get(item.risk_level, 9),
            queue_order(item),
            -(item.latest_fact_at.timestamp()) if item.latest_fact_at is not None else float("inf"),
            item.student_no,
        )
    )
    return result


@router.get(
    "/students/{student_id}",
    response_model=ProfileDetailResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def get_profile_student(
    student_id: int,
    term: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileDetailResponse:
    student = _assert_student_scope(
        db.execute(
            select(Student)
            .options(joinedload(Student.college), joinedload(Student.major))
            .where(Student.id == student_id)
        ).scalar_one_or_none(),
        user,
    )
    terms = db.execute(
        select(Profile.term)
        .where(Profile.student_id == student_id)
        .order_by(Profile.term.desc())
    ).scalars().all()
    if not terms:
        raise HTTPException(status_code=404, detail="profile not found")

    selected_term = term or terms[0]
    profile = db.execute(
        select(Profile).where(Profile.student_id == student_id, Profile.term == selected_term)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")

    assist_record_count = int(
        db.scalar(
            select(func.count(AssistRecord.id))
            .join(AssistCase, AssistRecord.case_id == AssistCase.id)
            .where(AssistCase.student_id == student.id)
        )
        or 0
    )

    match_feedback = match_feedback_by_student(db, [student.id]).get(student.id, [])
    # match_feedback_by_student 只返回 pushed_at 已写入的明确推送事实。
    journey_stage, journey_stage_index = derive_journey_stage(
        student.status,
        assist_record_count,
        match_feedback,
    )

    return ProfileDetailResponse(
        profile_id=profile.id,
        student_id=student.id,
        student_no=student.student_no,
        name=student.name,
        college_id=student.college_id,
        college_name=student.college.name,
        major_name=student.major.name,
        employment_status=student.status,
        status_occurred_at=student.status_occurred_at,
        assist_record_count=assist_record_count,
        journey_stage=journey_stage,
        journey_stage_index=journey_stage_index,
        term=profile.term,
        terms=terms,
        six_dims={k: float(v) for k, v in profile.six_dims_json.items()},
        narrative_text=profile.narrative_text or "",
        evidence=profile.evidence_json,
        evidence_snapshot_meta={
            "term": profile.term,
            "captured_at": profile.updated_at,
            "is_current_fact_chain": False,
        },
        fact_chain=build_student_fact_chain(db, student),
        llm_meta=profile.llm_meta,
        updated_at=profile.updated_at,
        match_feedback=match_feedback,
    )
