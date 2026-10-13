"""Schemas for T8 recruitment fairs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

FairStatusValue = Literal["计划中", "报名中", "签到进行中", "已结束", "已归档"]


class FairCreate(BaseModel):
    name: str = Field(min_length=1)
    fair_type: str = Field(default="综合招聘会", min_length=1, max_length=32)
    held_at: datetime | None = None
    location: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=96)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise ValueError("操作标识至少需要 8 个字符")
        return value


class FairUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    fair_type: str | None = Field(default=None, min_length=1, max_length=32)
    held_at: datetime | None = None
    location: str | None = None

    def update_dict(self) -> dict:
        return self.model_dump(exclude_unset=True)


class FairResponse(BaseModel):
    id: int
    name: str
    fair_type: str
    held_at: datetime | None
    location: str | None
    status: FairStatusValue
    registration_opens_at: datetime | None
    registration_closes_at: datetime | None
    checkin_started_at: datetime | None
    checkin_ended_at: datetime | None
    archived_at: datetime | None
    student_checkin_code_issued_at: datetime | None


class FairStatusUpdate(BaseModel):
    status: FairStatusValue


class FairStudentCheckinCodeIssue(BaseModel):
    reason: str = Field(min_length=6, max_length=200)
    idempotency_key: UUID
    expected_issued_at: datetime | None

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


class FairStudentCheckinCodeResponse(BaseModel):
    fair_id: int
    checkin_code: str
    issued_at: datetime
    valid_while_status: Literal["签到进行中"] = "签到进行中"


class FairCompanyCreate(BaseModel):
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    booth: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=96)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise ValueError("操作标识至少需要 8 个字符")
        return value


class FairCompanyReview(BaseModel):
    status: str = Field(min_length=1)
    booth: str | None = None


class FairCompanyResponse(BaseModel):
    id: int
    fair_id: int
    company: str
    booth: str | None
    status: str
    title: str | None
    job_id: int | None


class FairCheckinCreate(BaseModel):
    student_no: str = Field(min_length=1)
    gate_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=96)


class FairCheckinResponse(BaseModel):
    id: int
    fair_id: int
    student_id: int | None
    gate_id: str | None
    at: datetime
    source: str
    correction_reason: str | None = None


class FairCheckinCorrection(BaseModel):
    student_no: str = Field(min_length=1, max_length=32)
    gate_id: str | None = Field(default=None, max_length=32)
    at: datetime
    reason: str = Field(min_length=4, max_length=500)

    @field_validator("student_no")
    @classmethod
    def normalize_student_no(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("学号不能为空")
        return value

    @field_validator("gate_id")
    @classmethod
    def normalize_gate_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 4:
            raise ValueError("更正原因至少需要 4 个非空白字符")
        return value

    @field_validator("at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("签到时间必须包含时区")
        return value


class FairSnapshotResponse(BaseModel):
    fair_id: int
    status: FairStatusValue
    student_checkins: int
    enterprise_checkins: int
    registered_companies: int
    approved_companies: int
    frozen_at: datetime


class FairFunnelResponse(BaseModel):
    fair_id: int
    registered_companies: int
    approved_companies: int
    checked_in_students: int
    checked_in_enterprise_representatives: int
    applications: int
    interviews: int
    offers: int
    acceptances: int
    signings: int
