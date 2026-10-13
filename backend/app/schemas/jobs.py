"""T4 岗位解析与匹配 API schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.llm_batches import LLMBatchTaskResponse


class JobCreateRequest(BaseModel):
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)


class JobResponse(BaseModel):
    id: int
    company: str
    title: str
    source: str
    raw_text: str


class JobParseReviewRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    degree_req: str | None = None
    major_codes: list[str] | None = None
    direction_keywords: list[str] | None = None
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    headcount: int | None = None
    parse_confidence: float | None = None
    needs_review: bool | None = None
    review_reason: str | None = None

    def update_dict(self) -> dict:
        return self.model_dump(exclude_unset=True, exclude={"expected_revision"})


class JobParseResponse(BaseModel):
    id: int
    job_id: int
    degree_req: str | None
    major_codes: list[str]
    direction_keywords: list[str]
    location: str | None
    parse_confidence: float | None
    needs_review: bool
    review_reason: str | None
    review_revision: int


class JobParseQueuedResponse(BaseModel):
    status: str = "queued"
    job_id: int
    task_id: int


class JobBatchQueueRequest(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=1000)


JobParseDispatchResponse = JobParseResponse | JobParseQueuedResponse
JobBatchQueueResponse = LLMBatchTaskResponse


class ReviewQueueItem(BaseModel):
    parse_id: int
    job_id: int
    company: str
    title: str
    review_reason: str | None
    parse_confidence: float | None
    review_revision: int


class ParsedJobOption(BaseModel):
    parse_id: int
    job_id: int
    company: str
    title: str
    label: str
    degree_req: str | None
    major_names: list[str]
    location: str | None
    headcount: int | None
    recommended_count: int = 0
    pushed_count: int = 0
    parse_confidence: float | None
    received_at: datetime


class MatchResponse(BaseModel):
    id: int
    job_id: int
    student_id: int
    student_no: str
    student_name: str
    college_id: int
    college_name: str
    major_id: int
    score: float
    reason_text: str
    pushed_at: datetime | None
