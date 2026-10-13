"""T5 学生画像 API schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import MatchFeedbackSummary


class ProfileRebuildResponse(BaseModel):
    term: str
    rebuilt: int
    narrative_mode: str
    status: str = "completed"
    task_id: int | None = None


class StudentOutcomeUpdate(BaseModel):
    status: Literal["未落实", "签约", "升学", "出国", "入伍", "灵活就业", "暂不就业"]
    occurred_at: datetime | None = None


class StudentOutcomeResponse(BaseModel):
    student_id: int
    status: str
    occurred_at: datetime


class StudentFactOutcome(BaseModel):
    """学生主档中的实时去向与预警事实。"""

    student_id: int
    source_type: Literal["student"] = "student"
    source_id: int
    status: str
    occurred_at: datetime | None
    risk_level: str
    risk_reason: str | None


class StudentFactEvent(BaseModel):
    """按来源对象留痕的单个学生事实事件。"""

    student_id: int
    event_type: Literal[
        "assist_case_opened",
        "assist_recorded",
        "job_action",
        "job_pushed",
        "job_feedback",
        "outcome_confirmed",
    ]
    source_type: Literal["assist_case", "assist_record", "job_action", "match", "student"]
    source_id: int
    occurred_at: datetime | None
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)


class StudentFactChain(BaseModel):
    """同一 student_id 的实时业务事实链，而非画像生成时快照。"""

    student_id: int
    generated_at: datetime
    current_outcome: StudentFactOutcome
    events: list[StudentFactEvent] = Field(default_factory=list)


class ProfileEvidenceSnapshotMeta(BaseModel):
    """明确标记 evidence 为画像版本快照，防止被当作实时事实。"""

    source: Literal["profile.evidence_json"] = "profile.evidence_json"
    term: str
    captured_at: datetime
    is_current_fact_chain: Literal[False] = False


class ProfileListItem(BaseModel):
    student_id: int
    student_no: str
    name: str
    college_id: int
    college_name: str
    major_id: int
    major_name: str
    cohort_year: int
    risk_level: str
    status: str
    latest_term: str | None
    score_avg: float | None
    narrative_status: str
    journey_stage: Literal["发现", "帮扶", "推岗", "反馈", "结果"]
    assist_record_count: int = 0
    pushed_count: int = 0
    feedback_count: int = 0
    workflow_statuses: list[
        Literal["未帮扶", "已帮扶", "已推岗", "待反馈", "已反馈", "待确认结果"]
    ] = Field(default_factory=list)
    latest_fact_at: datetime | None = None


class ProfileDetailResponse(BaseModel):
    profile_id: int
    student_id: int
    student_no: str
    name: str
    college_id: int
    college_name: str
    major_name: str
    employment_status: str
    status_occurred_at: datetime | None
    assist_record_count: int
    journey_stage: Literal["发现", "帮扶", "推岗", "反馈", "结果"]
    journey_stage_index: int = Field(ge=0, le=4)
    term: str
    terms: list[str]
    six_dims: dict[str, float]
    narrative_text: str
    evidence: dict[str, Any]
    evidence_snapshot_meta: ProfileEvidenceSnapshotMeta
    fact_chain: StudentFactChain
    llm_meta: dict[str, Any]
    updated_at: datetime
    match_feedback: list[MatchFeedbackSummary] = Field(default_factory=list)
