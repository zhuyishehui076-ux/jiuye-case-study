"""Operational CLI for T12 persisted night batches."""

from __future__ import annotations

import argparse
import json
import sys
from time import perf_counter

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Job, Student
from app.services.llm_batches import (
    TASK_JOB_PARSE,
    TASK_PROFILE_NARRATIVE,
    batch_to_dict,
    enqueue_llm_batch,
    get_llm_batch,
    process_llm_batch,
)


def _summary(task: dict) -> dict:
    return {
        key: task.get(key)
        for key in (
            "id",
            "task_type",
            "status",
            "term",
            "total_items",
            "completed_items",
            "failed_items",
            "provider_name",
            "model_name",
            "started_at",
            "completed_at",
            "error_summary",
            "elapsed_seconds",
        )
        if key in task
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.llm.batch_worker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enqueue-unparsed-jobs", action="store_true")
    group.add_argument("--enqueue-profiles", metavar="TERM")
    group.add_argument("--task-id", type=int)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--actor", default="night-batch")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        if args.task_id is not None:
            task_id = get_llm_batch(db, args.task_id).id
        elif args.enqueue_unparsed_jobs:
            stmt = select(Job.id).where(~Job.parse.has()).order_by(Job.id)
            if args.limit:
                stmt = stmt.limit(args.limit)
            target_ids = list(db.execute(stmt).scalars())
            task_id = enqueue_llm_batch(
                db,
                task_type=TASK_JOB_PARSE,
                target_ids=target_ids,
                actor=args.actor,
            ).id
        else:
            stmt = select(Student.id).order_by(Student.id)
            if args.limit:
                stmt = stmt.limit(args.limit)
            target_ids = list(db.execute(stmt).scalars())
            task_id = enqueue_llm_batch(
                db,
                task_type=TASK_PROFILE_NARRATIVE,
                target_ids=target_ids,
                term=args.enqueue_profiles,
                actor=args.actor,
            ).id

        queued = batch_to_dict(get_llm_batch(db, task_id))
    print(
        json.dumps(
            {"event": "queued", "task": _summary(queued)},
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )

    if args.run:
        started = perf_counter()
        result = process_llm_batch(task_id)
        result["elapsed_seconds"] = round(perf_counter() - started, 3)
        print(
            json.dumps(
                {"event": "completed", "task": _summary(result)},
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
