"""PostgreSQL + FastAPI BackgroundTasks LLM night-batch orchestration (T12)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import record_db
from app.db.session import SessionLocal
from app.llm.provider import Provider, get_provider
from app.models import LLMBatchItem, LLMBatchTask
from app.services.operation_receipts import claim_operation, finish_operation
from app.services.write_idempotency import validate_write_operation_key

TASK_JOB_PARSE = "job_parse"
TASK_PROFILE_NARRATIVE = "profile_narrative"
SUPPORTED_TASK_TYPES = {TASK_JOB_PARSE, TASK_PROFILE_NARRATIVE}

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATUS_FAILED = "failed"
ACTIVE_STATUSES = {STATUS_QUEUED, STATUS_RUNNING}


def enqueue_llm_batch(
    db: Session,
    *,
    task_type: str,
    target_ids: list[int],
    actor: str,
    term: str | None = None,
    idempotency_key: str | None = None,
    operation_namespace: str = "llm_batch.enqueue",
    commit: bool = True,
    receipt_protected: bool = False,
) -> LLMBatchTask:
    if task_type not in SUPPORTED_TASK_TYPES:
        raise ValueError(f"unsupported task type: {task_type}")
    unique_ids = list(dict.fromkeys(target_ids))
    if not unique_ids:
        raise ValueError("batch target_ids cannot be empty")
    if task_type == TASK_PROFILE_NARRATIVE and not term:
        raise ValueError("profile narrative batch requires term")

    operation_key = idempotency_key or str(uuid4())
    receipt = None
    if receipt_protected:
        operation_key = validate_write_operation_key(operation_key)
    else:
        receipt, replayed = claim_operation(
            db,
            namespace=operation_namespace,
            idempotency_key=operation_key,
            actor=actor,
            payload={
                "task_type": task_type,
                "target_ids": unique_ids,
                "term": term,
            },
        )
        if replayed:
            task_id = receipt.result_json.get("task_id")
            if not isinstance(task_id, int):
                raise ValueError("idempotent batch receipt has no committed task")
            return get_llm_batch(db, task_id)

    task = LLMBatchTask(
        task_type=task_type,
        status=STATUS_QUEUED,
        term=term,
        total_items=len(unique_ids),
        completed_items=0,
        failed_items=0,
        created_by=actor,
        items=[
            LLMBatchItem(
                target_id=target_id,
                status=STATUS_QUEUED,
                created_by=actor,
            )
            for target_id in unique_ids
        ],
    )
    db.add(task)
    db.flush()
    if receipt is not None:
        finish_operation(receipt, result={"task_id": task.id})
    record_db(
        db,
        actor=actor,
        action="llm_batch.enqueue",
        object_type="llm_batch_task",
        object_id=task.id,
        correlation_id=f"LLM-BATCH-{operation_key}",
        change_summary={
            "task_type": task_type,
            "total_items": len(unique_ids),
            "term": term,
        },
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return get_llm_batch(db, task.id)


def get_llm_batch(db: Session, task_id: int) -> LLMBatchTask:
    task = db.execute(
        select(LLMBatchTask)
        .options(selectinload(LLMBatchTask.items))
        .where(LLMBatchTask.id == task_id)
    ).scalar_one_or_none()
    if task is None:
        raise ValueError("llm batch task not found")
    return task


def list_llm_batches(
    db: Session,
    *,
    task_type: str | None = None,
    limit: int = 20,
) -> list[LLMBatchTask]:
    stmt = (
        select(LLMBatchTask)
        .order_by(LLMBatchTask.id.desc())
        .limit(limit)
    )
    if task_type is not None:
        stmt = stmt.where(LLMBatchTask.task_type == task_type)
    return list(db.execute(stmt).scalars().all())


def list_pending_batch_ids(db: Session, *, limit: int = 20) -> list[int]:
    return list(
        db.execute(
            select(LLMBatchTask.id)
            .where(LLMBatchTask.status == STATUS_QUEUED)
            .order_by(LLMBatchTask.id)
            .limit(limit)
        ).scalars()
    )


def batch_to_dict(task: LLMBatchTask, *, include_items: bool = True) -> dict[str, Any]:
    payload = {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "term": task.term,
        "total_items": task.total_items,
        "completed_items": task.completed_items,
        "failed_items": task.failed_items,
        "provider_name": task.provider_name,
        "model_name": task.model_name,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "error_summary": task.error_summary,
    }
    payload["items"] = (
        [
            {
                "id": item.id,
                "target_id": item.target_id,
                "status": item.status,
                "result_id": item.result_id,
                "error_text": item.error_text,
                "started_at": item.started_at,
                "completed_at": item.completed_at,
            }
            for item in task.items
        ]
        if include_items
        else []
    )
    return payload


def _refresh_counts(db: Session, task: LLMBatchTask) -> None:
    completed, failed = db.execute(
        select(
            func.count().filter(LLMBatchItem.status == STATUS_COMPLETED),
            func.count().filter(LLMBatchItem.status == STATUS_FAILED),
        ).where(LLMBatchItem.task_id == task.id)
    ).one()
    task.completed_items = int(completed)
    task.failed_items = int(failed)


def _process_item(
    db: Session,
    *,
    task: LLMBatchTask,
    item: LLMBatchItem,
    provider: Provider,
) -> int:
    if task.task_type == TASK_JOB_PARSE:
        from app.services.jobs import parse_job

        parsed = parse_job(
            db,
            item.target_id,
            provider,
            actor=task.created_by or "llm-batch",
        )
        return parsed.id

    from app.services.profiles import rebuild_profile_for_student

    profile = rebuild_profile_for_student(
        db,
        item.target_id,
        term=task.term or "",
        actor=task.created_by or "llm-batch",
        provider=provider,
    )
    db.commit()
    if profile.llm_meta.get("status") != "generated":
        raise RuntimeError(str(profile.llm_meta.get("error") or "profile narrative failed"))
    return profile.id


def process_llm_batch(task_id: int, provider: Provider | None = None) -> dict[str, Any]:
    """Claim and process one persisted task; safe to call from BackgroundTasks or CLI."""
    with SessionLocal() as db:
        task = db.execute(
            select(LLMBatchTask)
            .where(LLMBatchTask.id == task_id)
            .with_for_update()
        ).scalar_one_or_none()
        if task is None:
            raise ValueError("llm batch task not found")
        if task.status != STATUS_QUEUED:
            return batch_to_dict(get_llm_batch(db, task_id))
        task.status = STATUS_RUNNING
        task.started_at = datetime.now(UTC)
        db.commit()

        try:
            active_provider = provider or get_provider()
        except Exception as exc:
            task = db.get(LLMBatchTask, task_id)
            if task is None:
                raise
            now = datetime.now(UTC)
            task.status = STATUS_FAILED
            task.failed_items = task.total_items
            task.completed_at = now
            task.error_summary = f"{type(exc).__name__}: {exc}"
            for item in db.execute(
                select(LLMBatchItem).where(LLMBatchItem.task_id == task_id)
            ).scalars():
                item.status = STATUS_FAILED
                item.completed_at = now
                item.error_text = task.error_summary
            db.commit()
            return batch_to_dict(get_llm_batch(db, task_id))

        task = db.get(LLMBatchTask, task_id)
        if task is None:
            raise ValueError("llm batch task not found")
        task.provider_name = active_provider.name
        task.model_name = getattr(active_provider, "model", None)
        item_ids = list(
            db.execute(
                select(LLMBatchItem.id)
                .where(LLMBatchItem.task_id == task_id)
                .order_by(LLMBatchItem.id)
            ).scalars()
        )
        db.commit()

        for item_id in item_ids:
            item = db.get(LLMBatchItem, item_id)
            task = db.get(LLMBatchTask, task_id)
            if item is None or task is None or item.status != STATUS_QUEUED:
                continue
            item.status = STATUS_RUNNING
            item.started_at = datetime.now(UTC)
            db.commit()
            try:
                result_id = _process_item(
                    db,
                    task=task,
                    item=item,
                    provider=active_provider,
                )
                item = db.get(LLMBatchItem, item_id)
                if item is None:
                    raise RuntimeError("llm batch item disappeared")
                item.status = STATUS_COMPLETED
                item.result_id = result_id
                item.completed_at = datetime.now(UTC)
                item.error_text = None
            except Exception as exc:
                db.rollback()
                item = db.get(LLMBatchItem, item_id)
                if item is None:
                    raise
                item.status = STATUS_FAILED
                item.completed_at = datetime.now(UTC)
                item.error_text = f"{type(exc).__name__}: {exc}"
            task = db.get(LLMBatchTask, task_id)
            if task is None:
                raise RuntimeError("llm batch task disappeared")
            _refresh_counts(db, task)
            db.commit()

        task = db.get(LLMBatchTask, task_id)
        if task is None:
            raise ValueError("llm batch task not found")
        _refresh_counts(db, task)
        task.status = (
            STATUS_COMPLETED
            if task.failed_items == 0
            else STATUS_COMPLETED_WITH_ERRORS
        )
        task.completed_at = datetime.now(UTC)
        task.error_summary = (
            None
            if task.failed_items == 0
            else f"{task.failed_items}/{task.total_items} items failed"
        )
        record_db(
            db,
            actor=task.created_by or "llm-batch",
            action="llm_batch.complete",
            object_type="llm_batch_task",
            object_id=task.id,
            change_summary={
                "status": task.status,
                "completed_items": task.completed_items,
                "failed_items": task.failed_items,
            },
        )
        db.commit()
        return batch_to_dict(get_llm_batch(db, task_id))
