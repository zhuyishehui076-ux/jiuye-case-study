"""Privacy-minimized enterprise H5 response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.auth_security import validate_password_policy

EnterpriseSubmissionStatus = Literal[
    "queued",
    "parsing",
    "review_pending",
    "matching",
    "completed",
    "failed",
]
ApplicationStatus = Literal[
    "submitted",
    "withdrawn",
    "interview",
    "offered",
    "rejected",
    "accepted",
]
EnterpriseTransitionTarget = Literal["interview", "offered", "rejected", "accepted"]


class EnterpriseTokenResponse(BaseModel):
    access_token: str
    organization_id: int
    account_id: int
    company: str
    username: str
    show_demo_badge: bool
    profile: Literal["production"]
    expires_at: datetime


class EnterpriseLoginRequest(BaseModel):
    username: str | None = Field(default=None, min_length=4, max_length=64)
    password: str | None = Field(default=None, min_length=8, max_length=72)
    activation_code: str | None = Field(default=None, min_length=16, max_length=128)
    enterprise_token: str | None = Field(default=None, min_length=24, max_length=256)

    @field_validator("password")
    @classmethod
    def bcrypt_limit(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 72:
            raise ValueError("密码不能超过 bcrypt 的 72 字节限制")
        return value

    @model_validator(mode="after")
    def require_one_mode(self) -> EnterpriseLoginRequest:
        has_demo = self.enterprise_token is not None
        has_password = self.username is not None and self.password is not None
        has_activation = (
            self.username is not None
            and self.activation_code is not None
            and self.password is not None
        )
        if sum([has_demo, has_password or has_activation]) != 1:
            raise ValueError("请使用演示令牌，或使用企业账号密码/激活材料登录")
        return self


class EnterpriseActivateRequest(BaseModel):
    username: str = Field(min_length=4, max_length=64)
    activation_code: str = Field(min_length=16, max_length=128)
    password: str = Field(min_length=12, max_length=72)

    @field_validator("password")
    @classmethod
    def bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码不能超过 bcrypt 的 72 字节限制")
        return value

    @model_validator(mode="after")
    def enforce_password_policy(self) -> EnterpriseActivateRequest:
        validate_password_policy(self.password, identity=self.username)
        return self


class EnterpriseLogoutResponse(BaseModel):
    ok: bool = True


class EnterpriseSessionResponse(BaseModel):
    company: str
    expires_at: datetime
    submit_limit_per_hour: int | None = None
    remaining_submissions: int | None = None
    show_demo_badge: bool
    profile: Literal["demo", "production"]
    organization_id: int | None = None
    account_id: int | None = None


class EnterpriseProfileResponse(BaseModel):
    organization_id: int
    legal_name: str
    short_name: str | None
    credit_code: str
    industry: str | None
    scale: str | None
    city: str | None
    address: str | None
    website: str | None
    summary: str | None
    contact_name: str | None
    status: Literal["pending", "verified", "suspended"]
    revision: int


class EnterpriseProfileUpdateRequest(BaseModel):
    short_name: str | None = Field(default=None, max_length=64)
    industry: str | None = Field(default=None, max_length=64)
    scale: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=128)
    website: str | None = Field(default=None, max_length=128)
    summary: str | None = Field(default=None, max_length=1000)
    contact_name: str | None = Field(default=None, max_length=64)
    expected_revision: int = Field(ge=1)
    idempotency_key: UUID

    @field_validator("idempotency_key")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("操作标识必须使用 UUIDv4")
        return value


class EnterpriseJobSubmitRequest(BaseModel):
    title: str = Field(min_length=2, max_length=128)
    raw_text: str = Field(min_length=20, max_length=20000)
    idempotency_key: UUID | None = None

    @field_validator("idempotency_key")
    @classmethod
    def require_uuid4(cls, value: UUID | None) -> UUID | None:
        if value is not None and value.version != 4:
            raise ValueError("操作标识必须使用 UUIDv4")
        return value


class EnterpriseSubmissionResponse(BaseModel):
    submission_id: int
    job_id: int
    company: str
    title: str
    status: EnterpriseSubmissionStatus
    parse_confidence: float | None
    degree_req: str | None
    major_codes: list[str]
    direction_keywords: list[str]
    location: str | None
    matched_candidate_count: int | None
    small_group_suppressed: bool
    recommendation_started: bool | None
    student_feedback_received: bool | None
    submitted_at: datetime
    updated_at: datetime
    error_message: str | None


class SupplyBucket(BaseModel):
    label: str
    count: int
    share: float


class HistoricalConversion(BaseModel):
    period: str
    total: int
    converted: int
    conversion_rate: float


class EnterpriseSupplyReportResponse(BaseModel):
    as_of: datetime
    total_students: int
    minimum_group_size: int
    majors: list[SupplyBucket]
    degrees: list[SupplyBucket]
    historical_conversion: list[HistoricalConversion]


class EnterpriseFairItem(BaseModel):
    fair_id: int
    fair_company_id: int
    name: str
    fair_type: str
    held_at: datetime | None
    location: str | None
    booth: str | None
    status: str
    registration_status: str
    can_checkin: bool
    checked_in_representatives: int


class EnterpriseFairRegistrationRequest(BaseModel):
    fair_id: int = Field(gt=0)
    title: str = Field(min_length=2, max_length=128)
    raw_text: str = Field(min_length=20, max_length=20000)
    idempotency_key: UUID

    @field_validator("idempotency_key")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("操作标识必须使用 UUIDv4")
        return value


class EnterpriseFairCheckinRequest(BaseModel):
    representative_name: str = Field(min_length=2, max_length=64)
    gate_id: str | None = Field(default=None, max_length=32)
    idempotency_key: str = Field(min_length=8, max_length=96)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("操作标识不能为空")
        return normalized


class EnterpriseFairCheckinResponse(BaseModel):
    checkin_id: int
    fair_id: int
    checked_in_at: datetime
    already_checked_in: bool


class EnterpriseCandidateItem(BaseModel):
    application_id: int
    job_id: int
    job_title: str
    name: str
    major_name: str
    degree_level: str
    cohort_year: int
    contact: str | None
    status: ApplicationStatus
    submitted_at: datetime
    revision: int


class EnterpriseCandidatePage(BaseModel):
    items: list[EnterpriseCandidateItem]
    total: int
    page: int
    page_size: int


class EnterpriseApplicationTransitionRequest(BaseModel):
    target_status: EnterpriseTransitionTarget
    reason: str = Field(min_length=4, max_length=200)
    revision: int = Field(ge=1)
    idempotency_key: UUID

    @field_validator("idempotency_key")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("操作标识必须使用 UUIDv4")
        return value


class EnterpriseApplicationTransitionResponse(BaseModel):
    application_id: int
    status: ApplicationStatus
    previous_status: ApplicationStatus
    revision: int
    already_applied: bool
