"""跨管理模块复用的精简响应结构。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MatchFeedbackSummary(BaseModel):
    match_id: int
    job_id: int
    company: str
    title: str
    pushed_at: datetime
    feedback: str | None
    updated_at: datetime
    feedback_at: datetime | None
