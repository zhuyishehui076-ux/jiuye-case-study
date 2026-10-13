"""T18 production student-account administration contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

StudentAccountStatus = Literal[
    "not_initialized",
    "pending_activation",
    "active",
    "suspended",
]
ActivationStatus = Literal["available", "consumed", "expired", "revoked"]


class StudentAccountItem(BaseModel):
    student_id: int
    student_no: str
    name: str
    college_name: str
    major_name: str | None
    cohort_year: int
    status: StudentAccountStatus
    revision: int
    activated_at: datetime | None
    last_login_at: datetime | None
    activation_id: int | None = None
    activation_status: ActivationStatus | None = None
    activation_expires_at: datetime | None = None


class StudentAccountPage(BaseModel):
    items: list[StudentAccountItem]
    total: int
    page: int
    page_size: int


class ActivationIssueTarget(BaseModel):
    student_id: int = Field(gt=0)
    expected_revision: int = Field(ge=0)
    purpose: Literal["activation", "recovery"]


class ActivationIssueRequest(BaseModel):
    targets: list[ActivationIssueTarget] = Field(min_length=1, max_length=200)
    expires_hours: int = Field(default=48, ge=1, le=168)
    reason: str = Field(min_length=6, max_length=200)
    idempotency_key: UUID

    @model_validator(mode="after")
    def unique_student_ids(self) -> ActivationIssueRequest:
        student_ids = [target.student_id for target in self.targets]
        if len(set(student_ids)) != len(student_ids):
            raise ValueError("学生列表不能包含重复项")
        self.targets = sorted(self.targets, key=lambda target: target.student_id)
        return self

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 6:
            raise ValueError("签发原因至少需要 6 个非空白字符")
        return normalized

    @field_validator("idempotency_key")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("操作标识必须使用 UUIDv4")
        return value


class ActivationIssuedItem(BaseModel):
    activation_id: int
    student_id: int
    student_no: str
    name: str
    activation_code: str
    expires_at: datetime


class ActivationIssueResponse(BaseModel):
    correlation_id: str
    items: list[ActivationIssuedItem]


class ActivationRevokeRequest(BaseModel):
    reason: str = Field(min_length=6, max_length=200)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 6:
            raise ValueError("吊销原因至少需要 6 个非空白字符")
        return normalized


class ActivationStateResponse(BaseModel):
    activation_id: int
    student_id: int
    status: ActivationStatus
    expires_at: datetime


class StudentAccountStateRequest(BaseModel):
    reason: str = Field(min_length=6, max_length=200)
    revision: int = Field(ge=1)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 6:
            raise ValueError("处理原因至少需要 6 个非空白字符")
        return normalized


class StudentAccountStateResponse(BaseModel):
    student_id: int
    status: StudentAccountStatus
    revision: int
    credential_version: int
