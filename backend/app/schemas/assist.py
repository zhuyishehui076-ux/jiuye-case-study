"""T6 一生一策帮扶台账 API schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import MatchFeedbackSummary


class AssistCaseResponse(BaseModel):
    id: int
    student_id: int
    student_no: str
    student_name: str
    college_id: int
    college_name: str
    major_id: int
    risk_reason: str | None
    opened_reason: str | None
    assigned_to: int | None
    status: str
    opened_at: datetime
    record_count: int
    action_summary: str
    employment_status: str
    status_occurred_at: datetime | None
    journey_stage: str
    journey_stage_index: int = Field(ge=0, le=4)
    match_feedback: list[MatchFeedbackSummary] = Field(default_factory=list)


class AssistRecordCreate(BaseModel):
    action_type: str = Field(min_length=1)
    note: str | None = None


class AssistRecordResponse(BaseModel):
    id: int
    case_id: int
    action_type: str
    note: str | None
    actor: str | None
    occurred_at: datetime
    replayed: bool
