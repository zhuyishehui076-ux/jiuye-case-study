"""D1: precompute profile narratives with the configured LLM provider.

Runs student profiles in red -> yellow -> green priority order. Individual LLM
failures are converted back to queued profile narratives by the service layer
and counted in the final report; they do not abort the batch.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.db.session import SessionLocal  # noqa: E402
from app.llm.provider import get_provider  # noqa: E402
from app.models import Profile, Student  # noqa: E402
from app.seed.demo import run_demo_seed  # noqa: E402
from app.services.profiles import rebuild_profile_for_student  # noqa: E402
from sqlalchemy import select  # noqa: E402

RISK_PRIORITY = {"red": 0, "yellow": 1, "green": 2}


def _student_order(rows: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return sorted(rows, key=lambda row: (RISK_PRIORITY.get(row[1], 9), row[0]))


def _sample_profiles(term: str) -> dict[str, dict[str, Any] | None]:
    samples: dict[str, dict[str, Any] | None] = {"red": None, "yellow": None, "green": None}
    with SessionLocal() as db:
        for risk in samples:
            row = db.execute(
                select(Student, Profile)
                .join(Profile, Profile.student_id == Student.id)
                .where(Student.risk_level == risk, Profile.term == term)
                .order_by(Student.id)
            ).first()
            if row is None:
                continue
            student, profile = row
            samples[risk] = {
                "student_id": student.id,
                "student_no": student.student_no,
                "name": student.name,
                "risk_level": student.risk_level,
                "status": student.status,
                "llm_status": profile.llm_meta.get("status"),
                "narrative_text": profile.narrative_text,
                "suggestions": profile.evidence_json.get("suggestions", []),
            }
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--term", default="2024-2025-1")
    parser.add_argument("--actor", default="d1-precompute")
    parser.add_argument("--reset-seed", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    started = time.perf_counter()
    seed_counts: dict[str, int] | None = None
    with SessionLocal() as db:
        if args.reset_seed:
            seed_counts = run_demo_seed(db, reset=True)
        rows = db.execute(select(Student.id, Student.risk_level)).all()
    ordered = _student_order([(int(student_id), str(risk)) for student_id, risk in rows])
    if args.limit is not None:
        ordered = ordered[: args.limit]

    provider = get_provider()
    stats = {
        "generated": 0,
        "queued": 0,
        "failed_queued": 0,
        "errors": 0,
    }
    error_samples: list[dict[str, Any]] = []

    for idx, (student_id, risk) in enumerate(ordered, start=1):
        item_started = time.perf_counter()
        try:
            with SessionLocal() as db:
                profile = rebuild_profile_for_student(
                    db,
                    student_id,
                    term=args.term,
                    actor=args.actor,
                    provider=provider,
                )
                db.commit()
                status = str(profile.llm_meta.get("status", "unknown"))
        except Exception as exc:  # noqa: BLE001 - batch must continue and report raw failures
            stats["errors"] += 1
            status = "exception_queued"
            try:
                with SessionLocal() as db:
                    profile = rebuild_profile_for_student(
                        db,
                        student_id,
                        term=args.term,
                        actor=args.actor,
                        provider=None,
                    )
                    profile.llm_meta = {
                        **profile.llm_meta,
                        "status": "exception_queued",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    db.commit()
            except Exception as queue_exc:  # noqa: BLE001
                status = "exception_unqueued"
                if len(error_samples) < 10:
                    error_samples.append(
                        {
                            "student_id": student_id,
                            "risk_level": risk,
                            "error": f"{type(exc).__name__}: {exc}",
                            "queue_error": f"{type(queue_exc).__name__}: {queue_exc}",
                        }
                    )
            else:
                if len(error_samples) < 10:
                    error_samples.append(
                        {
                            "student_id": student_id,
                            "risk_level": risk,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        if status in stats:
            stats[status] += 1
        else:
            stats.setdefault(status, 0)
            stats[status] += 1

        if args.progress_every and (idx % args.progress_every == 0 or idx == len(ordered)):
            print(
                json.dumps(
                    {
                        "stage": "profile_narrative_progress",
                        "done": idx,
                        "total": len(ordered),
                        "risk_level": risk,
                        "last_student_id": student_id,
                        "last_status": status,
                        "last_elapsed_seconds": round(time.perf_counter() - item_started, 3),
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    elapsed = time.perf_counter() - started
    summary = {
        "stage": "profile_narrative_precompute_complete",
        "term": args.term,
        "provider": provider.name,
        "model": getattr(provider, "model", None),
        "total": len(ordered),
        "stats": stats,
        "failure_count": stats.get("queued", 0)
        + stats.get("failed_queued", 0)
        + stats.get("exception_queued", 0)
        + stats.get("exception_unqueued", 0),
        "elapsed_seconds": round(elapsed, 3),
        "elapsed_minutes": round(elapsed / 60, 3),
        "seed_counts": seed_counts,
        "samples": _sample_profiles(args.term),
        "error_samples": error_samples,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
