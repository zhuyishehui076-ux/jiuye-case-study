"""S1 student-scope API schemas; deliberately excludes management risk fields."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class StudentLoginRequest(BaseModel):
    wx_code: str | None = Field(default=None, max_length=256)
    binding_code: str | None = Field(default=None, min_length=8, max_length=128)
    student_no: str | None = Field(default=None, min_length=1, max_length=32)
    password: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_bcrypt_password_bytes(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 72:
            raise ValueError("密码按 UTF-8 计不能超过 72 字节")
        return value


class StudentActivationConsents(BaseModel):
    recommendation_enabled: bool
    growth_profile_enabled: bool
    job_action_tracking_enabled: bool


class StudentActivationRequest(BaseModel):
    student_no: str = Field(min_length=1, max_length=32)
    activation_code: str = Field(min_length=16, max_length=128)
    password: str = Field(min_length=12, max_length=128)
    consents: StudentActivationConsents

    @field_validator("student_no", "activation_code")
    @classmethod
    def normalize_identity_value(cls, value: str) -> str:
        return value.strip()

    @field_validator("password")
    @classmethod
    def validate_bcrypt_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码按 UTF-8 计不能超过 72 字节")
        return value

    @model_validator(mode="after")
    def validate_password_policy(self) -> StudentActivationRequest:
        password = self.password
        if self.student_no and self.student_no.casefold() in password.casefold():
            raise ValueError("密码不能包含完整学号")
        if not (
            re.search(r"[a-z]", password)
            and re.search(r"[A-Z]", password)
            and re.search(r"\d", password)
            and re.search(r"[^A-Za-z0-9]", password)
        ):
            raise ValueError("密码须同时包含大写字母、小写字母、数字和符号")
        return self


class StudentTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    student_id: int
    student_no: str
    name: str
    show_demo_badge: bool
    profile: Literal["demo", "production"] = "demo"
    expires_at: datetime | None = None


class StudentLogoutResponse(BaseModel):
    logged_out: bool = True


class StudentMeResponse(BaseModel):
    student_no: str
    name: str
    college_name: str
    major_name: str
    class_name: str | None
    cohort_year: int
    degree_level: str
    contact: str | None
    employment_status: str


class RecommendationItem(BaseModel):
    match_id: int
    job_id: int
    company: str
    title: str
    score: float
    reason_text: str
    feedback: Literal["已读", "已投", "不合适"] | None
    pushed_at: datetime
    location: str | None = None
    degree_req: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    deadline: date | None = None
    application_status: str | None = None


class RecommendationResponse(BaseModel):
    enabled: bool
    items: list[RecommendationItem]


class FeedbackUpdate(BaseModel):
    feedback: Literal["已读", "已投", "不合适"]


class FeedbackResponse(BaseModel):
    match_id: int
    feedback: Literal["已读", "已投", "不合适"]


class GrowthResponse(BaseModel):
    enabled: bool
    student_id: int
    name: str
    major_name: str
    term: str | None
    six_dims: dict[str, float]
    summary: str
    suggestions: list[str]


class ConsentPayload(BaseModel):
    recommendation_enabled: bool
    growth_profile_enabled: bool
    job_action_tracking_enabled: bool


class ConsentState(ConsentPayload):
    revision: int = Field(ge=0)


class ConsentUpdateRequest(ConsentPayload):
    expected_revision: int = Field(ge=0)


class JobActionCreate(BaseModel):
    # 两段文字会以“企业 · 岗位”合并写入 128 字符的业务字段；单项 60
    # 字符可给分隔符和后续兼容留出空间，避免合法请求在数据库层变成 500。
    company: str = Field(min_length=1, max_length=60)
    job_title: str = Field(min_length=1, max_length=60)
    occurred_at: datetime | None = None


class JobActionResponse(BaseModel):
    action_id: int
    action_type: Literal["投递"]
    target: str
    occurred_at: datetime


class StudentJobDetailResponse(BaseModel):
    job_id: int
    match_id: int | None
    company: str
    title: str
    raw_text: str
    received_at: datetime
    location: str | None
    degree_req: str | None
    salary_min: int | None
    salary_max: int | None
    deadline: date | None
    score: float | None
    reason_text: str | None
    feedback: Literal["已读", "已投", "不合适"] | None
    application_status: str | None
    application_id: int | None
    disclosure_authorized: bool | None
    application_revision: int | None


class StudentApplicationCreate(BaseModel):
    job_id: int = Field(gt=0)
    match_id: int | None = Field(default=None, gt=0)
    fair_id: int | None = Field(default=None, gt=0)
    disclose_to_employer: bool
    idempotency_key: UUID

    @field_validator("idempotency_key")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("操作标识必须使用 UUIDv4")
        return value


class StudentDisclosureUpdate(BaseModel):
    authorized: bool
    revision: int = Field(gt=0)
    idempotency_key: UUID

    @field_validator("idempotency_key")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("操作标识必须使用 UUIDv4")
        return value


class StudentApplicationResponse(BaseModel):
    application_id: int
    job_id: int
    match_id: int | None
    company: str
    title: str
    status: str
    submitted_at: datetime
    disclosure_authorized: bool
    already_submitted: bool = False
    revision: int


class StudentProgressEvent(BaseModel):
    event_id: str
    kind: Literal["application", "feedback", "fair", "outcome"]
    title: str
    detail: str
    status: str
    occurred_at: datetime


class StudentNextAction(BaseModel):
    title: str
    description: str
    target_path: str


class StudentProgressResponse(BaseModel):
    stage: str
    employment_status: str
    application_count: int
    fair_checkin_count: int
    next_action: StudentNextAction
    events: list[StudentProgressEvent]


class StudentFairJobItem(BaseModel):
    job_id: int
    match_id: int
    company: str
    title: str


class StudentFairItem(BaseModel):
    fair_id: int
    name: str
    fair_type: str
    held_at: datetime | None
    location: str | None
    status: Literal["计划中", "报名中", "签到进行中", "已结束", "已归档"]
    can_checkin: bool
    checked_in: bool
    checked_in_at: datetime | None
    checkin_id: int | None = None
    jobs: list[StudentFairJobItem] = Field(default_factory=list)


class StudentFairCheckinRequest(BaseModel):
    gate_id: str | None = Field(default=None, max_length=32)
    idempotency_key: str = Field(min_length=8, max_length=96)
    checkin_code: str | None = Field(default=None, min_length=6, max_length=128)


class StudentFairCheckinResponse(BaseModel):
    checkin_id: int
    fair_id: int
    checked_in_at: datetime
    already_checked_in: bool
