"""T4 M4 岗位解析与智能匹配 API。"""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_roles
from app.api.upload_guard import read_validated_xlsx, require_user_file_uploads
from app.core.audit import record_db
from app.core.config import settings
from app.core.rbac import Role
from app.db.session import get_db
from app.llm.provider import get_provider
from app.models import Job, JobParse
from app.schemas.jobs import (
    JobBatchQueueRequest,
    JobBatchQueueResponse,
    JobCreateRequest,
    JobParseDispatchResponse,
    JobParseQueuedResponse,
    JobParseResponse,
    JobParseReviewRequest,
    JobResponse,
    MatchResponse,
    ParsedJobOption,
    ReviewQueueItem,
)
from app.services.downloads import chinese_attachment
from app.services.jobs import (
    JobParseReviewConflictError,
    MatchForbiddenError,
    MatchInputNotFoundError,
    MatchNotEligibleError,
    MatchNotFoundError,
    calculate_matches_for_job,
    confirm_push_matches,
    create_job,
    export_matches_csv,
    import_jobs_from_xlsx,
    list_matches_for_job,
    list_successful_parsed_jobs,
    parse_job,
    review_job_parse,
)
from app.services.llm_batches import (
    TASK_JOB_PARSE,
    batch_to_dict,
    enqueue_llm_batch,
    get_llm_batch,
)
from app.services.operation_receipts import (
    OperationReceiptConflict,
    claim_operation,
    finish_operation,
    get_operation_result,
)
from app.services.write_idempotency import InvalidWriteOperationKey

router = APIRouter(prefix="/jobs", tags=["jobs"])

PUSH_ROLES = (Role.EMPLOYMENT_OFFICE, Role.COUNSELOR)
ALL_READ_ROLES = (
    Role.EMPLOYMENT_OFFICE,
    Role.COUNSELOR,
)


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        company=job.company,
        title=job.title,
        source=job.source,
        raw_text=job.raw_text,
    )


def _parse_response(parse: JobParse) -> JobParseResponse:
    return JobParseResponse(
        id=parse.id,
        job_id=parse.job_id,
        degree_req=parse.degree_req,
        major_codes=parse.major_codes,
        direction_keywords=parse.direction_keywords,
        location=parse.location,
        parse_confidence=float(parse.parse_confidence) if parse.parse_confidence is not None else None,
        needs_review=parse.needs_review,
        review_reason=parse.review_reason,
        review_revision=parse.review_revision,
    )


@router.post(
    "/paste",
    response_model=JobResponse,
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def paste_job(
    payload: JobCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobResponse:
    try:
        job = create_job(
            db,
            company=payload.company,
            title=payload.title,
            raw_text=payload.raw_text,
            actor=user.username,
            idempotency_key=idempotency_key,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_response(job)


@router.get(
    "/paste-operations/{idempotency_key}",
    response_model=JobResponse,
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def get_paste_operation(
    idempotency_key: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobResponse:
    try:
        result = get_operation_result(
            db,
            namespace="job.create",
            idempotency_key=idempotency_key,
            actor=user.username,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = result.get("job_id") if result is not None else None
    job = db.get(Job, job_id) if isinstance(job_id, int) else None
    if job is None:
        raise HTTPException(status_code=404, detail="paste operation not found")
    return _job_response(job)


@router.post(
    "/import-xlsx",
    response_model=list[JobResponse],
    dependencies=[
        Depends(require_roles(Role.EMPLOYMENT_OFFICE)),
        Depends(require_user_file_uploads),
    ],
)
async def import_xlsx(
    file: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[JobResponse]:
    try:
        jobs = import_jobs_from_xlsx(
            db,
            await read_validated_xlsx(file),
            actor=user.username,
            idempotency_key=idempotency_key,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [_job_response(job) for job in jobs]


@router.post(
    "/email-pull",
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def email_pull_reserved() -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="邮箱拉取为预留接口，v1 不实现",
    )


@router.post(
    "/queue-parse",
    response_model=JobBatchQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def queue_job_parses(
    payload: JobBatchQueueRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    unique_ids = list(dict.fromkeys(payload.job_ids))
    try:
        receipt, replayed = claim_operation(
            db,
            namespace="job.queue_parse",
            idempotency_key=idempotency_key,
            actor=user.username,
            payload={"job_ids": unique_ids},
        )
        if replayed:
            task_id = receipt.result_json.get("task_id")
            if not isinstance(task_id, int):
                raise ValueError("idempotent queue receipt has no committed task")
            return batch_to_dict(get_llm_batch(db, task_id))
        if not settings.llm_batch_enabled:
            raise HTTPException(status_code=409, detail="当前档位未启用无 GPU 夜间批处理")
        existing_ids = set(
            db.execute(select(Job.id).where(Job.id.in_(unique_ids))).scalars()
        )
        missing = sorted(set(unique_ids) - existing_ids)
        if missing:
            raise HTTPException(status_code=404, detail=f"jobs not found: {missing}")
        task = enqueue_llm_batch(
            db,
            task_type=TASK_JOB_PARSE,
            target_ids=unique_ids,
            actor=user.username,
            idempotency_key=idempotency_key,
            commit=False,
            receipt_protected=True,
        )
        finish_operation(receipt, result={"task_id": task.id})
        db.commit()
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return batch_to_dict(task)


@router.post(
    "/{job_id}/parse",
    response_model=JobParseDispatchResponse,
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def parse_job_endpoint(
    job_id: int,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobParseDispatchResponse:
    try:
        receipt, replayed = claim_operation(
            db,
            namespace="job.parse_dispatch",
            idempotency_key=idempotency_key,
            actor=user.username,
            payload={"job_id": job_id},
        )
        if replayed:
            result_kind = receipt.result_json.get("kind")
            if result_kind == "batch":
                task_id = receipt.result_json.get("task_id")
                if not isinstance(task_id, int):
                    raise ValueError("idempotent parse receipt has no committed task")
                get_llm_batch(db, task_id)
                response.status_code = status.HTTP_202_ACCEPTED
                return JobParseQueuedResponse(job_id=job_id, task_id=task_id)
            parse_id = receipt.result_json.get("parse_id")
            parse = db.get(JobParse, parse_id) if isinstance(parse_id, int) else None
            if result_kind != "sync" or parse is None:
                raise ValueError("idempotent parse receipt has no committed parse")
            return _parse_response(parse)

        if settings.llm_batch_enabled:
            if db.get(Job, job_id) is None:
                raise HTTPException(status_code=404, detail="job not found")
            task = enqueue_llm_batch(
                db,
                task_type=TASK_JOB_PARSE,
                target_ids=[job_id],
                actor=user.username,
                idempotency_key=idempotency_key,
                commit=False,
                receipt_protected=True,
            )
            finish_operation(
                receipt,
                result={"kind": "batch", "task_id": task.id},
            )
            db.commit()
            response.status_code = status.HTTP_202_ACCEPTED
            return JobParseQueuedResponse(job_id=job_id, task_id=task.id)

        parse = parse_job(
            db,
            job_id,
            get_provider(),
            actor=user.username,
            commit=False,
            correlation_id=f"JOB-PARSE-{idempotency_key}",
        )
        finish_operation(
            receipt,
            result={"kind": "sync", "parse_id": parse.id},
        )
        db.commit()
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _parse_response(parse)


@router.get(
    "/review-queue",
    response_model=list[ReviewQueueItem],
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def review_queue(db: Session = Depends(get_db)) -> list[ReviewQueueItem]:
    rows = db.execute(
        select(JobParse, Job)
        .join(Job, JobParse.job_id == Job.id)
        .where(JobParse.needs_review.is_(True))
        .order_by(JobParse.id)
    ).all()
    return [
        ReviewQueueItem(
            parse_id=parse.id,
            job_id=job.id,
            company=job.company,
            title=job.title,
            review_reason=parse.review_reason,
            parse_confidence=float(parse.parse_confidence)
            if parse.parse_confidence is not None
            else None,
            review_revision=parse.review_revision,
        )
        for parse, job in rows
    ]


@router.get(
    "/parsed",
    response_model=list[ParsedJobOption],
    dependencies=[Depends(require_roles(*ALL_READ_ROLES))],
)
def parsed_jobs(db: Session = Depends(get_db)) -> list[ParsedJobOption]:
    return [ParsedJobOption(**row) for row in list_successful_parsed_jobs(db)]


@router.put(
    "/parses/{parse_id}/review",
    response_model=JobParseResponse,
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def review_parse_endpoint(
    parse_id: int,
    payload: JobParseReviewRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobParseResponse:
    try:
        updates = payload.update_dict()
        receipt, replayed = claim_operation(
            db,
            namespace="job.parse_review",
            idempotency_key=idempotency_key,
            actor=user.username,
            payload={
                "parse_id": parse_id,
                "expected_revision": payload.expected_revision,
                "updates": updates,
            },
        )
        if replayed:
            stored_parse_id = receipt.result_json.get("parse_id")
            parse = (
                db.get(JobParse, stored_parse_id)
                if isinstance(stored_parse_id, int)
                else None
            )
            if parse is None:
                raise ValueError("idempotent review receipt has no committed parse")
        else:
            parse = review_job_parse(
                db,
                parse_id,
                actor=user.username,
                updates=updates,
                expected_revision=payload.expected_revision,
                commit=False,
                correlation_id=f"JOB-PARSE-REVIEW-{idempotency_key}",
            )
            finish_operation(receipt, result={"parse_id": parse.id})
            db.commit()
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except JobParseReviewConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "job_parse_review_conflict",
                "message": "岗位信息已被其他页面更新，本次未覆盖较新事实",
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _parse_response(parse)


@router.post(
    "/{job_id}/match",
    response_model=list[MatchResponse],
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def calculate_matches_endpoint(
    job_id: int,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MatchResponse]:
    try:
        receipt, replayed = claim_operation(
            db,
            namespace="job.match_calculate",
            idempotency_key=idempotency_key,
            actor=user.username,
            payload={"job_id": job_id},
        )
        if not replayed:
            calculate_matches_for_job(
                db,
                job_id,
                actor=user.username,
                correlation_id=f"JOB-MATCH-{idempotency_key}",
            )
            finish_operation(receipt, result={"job_id": job_id})
            db.commit()
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OperationReceiptConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MatchInputNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [MatchResponse(**row) for row in list_matches_for_job(db, job_id)]


@router.get(
    "/{job_id}/match-operations/{idempotency_key}",
    response_model=list[MatchResponse],
    dependencies=[Depends(require_roles(Role.EMPLOYMENT_OFFICE))],
)
def get_match_operation_endpoint(
    job_id: int,
    idempotency_key: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MatchResponse]:
    try:
        result = get_operation_result(
            db,
            namespace="job.match_calculate",
            idempotency_key=idempotency_key,
            actor=user.username,
        )
    except InvalidWriteOperationKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None or result.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="match operation not found")
    return [MatchResponse(**row) for row in list_matches_for_job(db, job_id)]


@router.get(
    "/{job_id}/matches",
    response_model=list[MatchResponse],
    dependencies=[Depends(require_roles(*ALL_READ_ROLES))],
)
def list_matches_endpoint(
    job_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MatchResponse]:
    college_id = user.college_id if user.role is Role.COUNSELOR else None
    return [
        MatchResponse(**row)
        for row in list_matches_for_job(db, job_id, college_id=college_id)
    ]


@router.post(
    "/{job_id}/push",
    dependencies=[Depends(require_roles(*PUSH_ROLES))],
)
def push_matches_endpoint(
    job_id: int,
    student_id: int | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    college_id = user.college_id if user.role is Role.COUNSELOR else None
    if user.role is Role.COUNSELOR and student_id is None:
        raise HTTPException(status_code=409, detail="辅导员必须先选择一名本学院学生再定向推岗。")
    try:
        return confirm_push_matches(
            db,
            job_id,
            actor=user.username,
            college_id=college_id,
            student_id=student_id,
        )
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MatchNotEligibleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MatchForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get(
    "/{job_id}/export",
    dependencies=[Depends(require_roles(*ALL_READ_ROLES))],
)
def export_matches_endpoint(
    job_id: int,
    header_language: Literal["zh", "en"] = Query(default="zh"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if db.scalar(select(Job.id).where(Job.id == job_id)) is None:
        raise HTTPException(status_code=404, detail="没有找到该岗位。")
    college_id = user.college_id if user.role is Role.COUNSELOR else None
    rows = list_matches_for_job(db, job_id, college_id=college_id, positive_only=True)
    csv_text = export_matches_csv(rows, header_language=header_language)
    record_db(
        db,
        actor=user.username,
        actor_id=user.user_id,
        actor_role=user.role.value,
        source_channel="management-web",
        correlation_id=f"JOB-{job_id}-EXPORT-{uuid4().hex[:12]}",
        action="job_match.export",
        object_type="job",
        object_id=job_id,
        change_summary={
            "format": "csv",
            "header_language": header_language,
            "row_count": len(rows),
            "college_scope": college_id,
        },
    )
    db.commit()
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": chinese_attachment(
            f"岗位{job_id}推荐学生名单.csv" if header_language == "zh" else f"job-{job_id}-matches-en.csv",
            fallback=f"job-{job_id}.csv" if header_language == "zh" else f"job-{job_id}-matches-en.csv",
        )},
    )
