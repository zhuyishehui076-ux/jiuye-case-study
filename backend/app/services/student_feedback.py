"""学生岗位反馈的管理端聚合视图。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, Match
from app.models.enums import MatchFeedback

_STORED_TO_PUBLIC = {
    MatchFeedback.READ.value: "已读",
    MatchFeedback.APPLIED.value: "已投",
    MatchFeedback.UNSUITABLE.value: "不合适",
}
_PUBLIC_TO_STORED = {value: key for key, value in _STORED_TO_PUBLIC.items()}


def public_feedback(value: str | None) -> str | None:
    return _STORED_TO_PUBLIC.get(value or "")


def stored_feedback(value: str) -> str:
    try:
        return _PUBLIC_TO_STORED[value]
    except KeyError as exc:
        raise ValueError("unsupported match feedback") from exc


def match_feedback_by_student(
    db: Session,
    student_ids: Iterable[int],
) -> dict[int, list[dict[str, Any]]]:
    ids = sorted(set(student_ids))
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not ids:
        return grouped
    rows = db.execute(
        select(Match, Job)
        .join(Job, Match.job_id == Job.id)
        .where(Match.student_id.in_(ids), Match.pushed_at.is_not(None))
        .order_by(Match.pushed_at.desc(), Match.id.desc())
    ).all()
    for match, job in rows:
        label = public_feedback(match.feedback)
        if match.student_id is None or match.pushed_at is None:
            continue
        grouped[match.student_id].append(
            {
                "match_id": match.id,
                "job_id": job.id,
                "company": job.company,
                "title": job.title,
                "pushed_at": match.pushed_at,
                "feedback": label,
                "updated_at": match.updated_at,
                "feedback_at": match.feedback_at,
            }
        )
    return grouped
