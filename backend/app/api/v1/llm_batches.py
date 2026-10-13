"""T12 no-GPU LLM night-batch status and dispatch API."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.rbac import Role
from app.db.session import get_db
from app.schemas.llm_batches import LLMBatchTaskResponse, RunPendingResponse
from app.services.llm_batches import (
    STATUS_QUEUED,
    batch_to_dict,
    get_llm_batch,
    list_llm_batches,
    list_pending_batch_ids,
    process_llm_batch,
)

router = APIRouter(prefix="/llm-batches", tags=["llm-batches"])

READ_ROLES = (
    Role.EMPLOYMENT_OFFICE,
)
RUN_ROLES = (Role.EMPLOYMENT_OFFICE,)


@router.get(
    "",
    response_model=list[LLMBatchTaskResponse],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_batches(
    task_type: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [
        batch_to_dict(task, include_items=False)
        for task in list_llm_batches(db, task_type=task_type, limit=limit)
    ]


@router.get(
    "/{task_id}",
    response_model=LLMBatchTaskResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def get_batch(task_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return batch_to_dict(get_llm_batch(db, task_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/run-pending",
    response_model=RunPendingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*RUN_ROLES))],
)
def run_pending(
    background_tasks: BackgroundTasks,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> RunPendingResponse:
    task_ids = list_pending_batch_ids(db, limit=limit)
    for task_id in task_ids:
        background_tasks.add_task(process_llm_batch, task_id)
    return RunPendingResponse(status="started", task_ids=task_ids)


@router.post(
    "/{task_id}/run",
    response_model=LLMBatchTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*RUN_ROLES))],
)
def run_batch(
    task_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    try:
        task = get_llm_batch(db, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if task.status != STATUS_QUEUED:
        raise HTTPException(status_code=409, detail=f"batch status is {task.status}")
    background_tasks.add_task(process_llm_batch, task_id)
    return batch_to_dict(task)
