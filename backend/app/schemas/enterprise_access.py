"""T19 production enterprise-account administration contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

EnterpriseAccountStatus = Literal[
    "not_initialized",
    "pending_activation",
    "active",
    "suspended",
]
EnterpriseOrganizationStatus = Literal["pending", "verified", "suspended"]
ActivationStatus = Literal["available", "consumed", "expired", "revoked"]


class EnterpriseAccountItem(BaseModel):
    organization_id: int
    legal_name: str
    short_name: str | None
    credit_code: str
    organization_status: EnterpriseOrganizationStatus
    organization_revision: int
    account_id: int | None
    username: str | None
    status: EnterpriseAccountStatus
    revision: int
    activated_at: datetime | None
    last_login_at: datetime | None
    activation_id: int | None = None
    activation_status: ActivationStatus | None = None
    activation_expires_at: datetime | None = None


class EnterpriseAccountPage(BaseModel):
    items: list[EnterpriseAccountItem]
    total: int
    page: int
    page_size: int


class EnterpriseOrganizationUpsertRequest(BaseModel):
    legal_name: str = Field(min_length=2, max_length=128)
    short_name: str | None = Field(default=None, max_length=64)
    credit_code: str = Field(min_length=6, max_length=64)
    username: str = Field(min_length=4, max_length=64)
    industry: str | None = Field(default=None, max_length=64)
    scale: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=128)
    website: str | None = Field(default=None, max_length=128)
    summary: str | None = Field(default=None, max_length=1000)
    contact_name: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=64)
    idempotency_key: UUID

    @field_validator(
        "legal_name",
        "short_name",
        "credit_code",
        "username",
        "industry",
        "scale",
        "city",
        "address",
        "website",
        "summary",
        "contact_name",
        "contact_phone",
        mode="before",
    )
    @classmethod
    def normalize_strings(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("idempotency_key")
    @classmethod
    def require_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("操作标识必须使用 UUIDv4")
        return value

    @model_validator(mode="after")
    def require_nonempty_core(self) -> EnterpriseOrganizationUpsertRequest:
        if not self.legal_name or not self.credit_code or not self.username:
            raise ValueError("企业名称、统一社会信用代码和登录名不能为空")
        return self


class EnterpriseOrganizationUpsertResponse(BaseModel):
    organization_id: int
    account_id: int
    legal_name: str
    username: str
    organization_revision: int
    account_revision: int


class EnterpriseActivationIssueTarget(BaseModel):
    organization_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    expected_revision: int = Field(ge=0)
    purpose: Literal["activation", "recovery"]


class EnterpriseActivationIssueRequest(BaseModel):
    targets: list[EnterpriseActivationIssueTarget] = Field(min_length=1, max_length=200)
    expires_hours: int = Field(default=48, ge=1, le=168)
    reason: str = Field(min_length=6, max_length=200)
    idempotency_key: UUID

    @model_validator(mode="after")
    def unique_accounts(self) -> EnterpriseActivationIssueRequest:
        ids = [target.account_id for target in self.targets]
        if len(set(ids)) != len(ids):
            raise ValueError("企业账号列表不能包含重复项")
        self.targets = sorted(self.targets, key=lambda target: target.organization_id)
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


class EnterpriseActivationIssuedItem(BaseModel):
    activation_id: int
    organization_id: int
    account_id: int
    username: str
    company: str
    activation_code: str
    expires_at: datetime


class EnterpriseActivationIssueResponse(BaseModel):
    correlation_id: str
    items: list[EnterpriseActivationIssuedItem]


class EnterpriseAccountStateRequest(BaseModel):
    reason: str = Field(min_length=6, max_length=200)
    revision: int = Field(ge=1)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 6:
            raise ValueError("处理原因至少需要 6 个非空白字符")
        return normalized


class EnterpriseAccountStateResponse(BaseModel):
    account_id: int
    organization_id: int
    status: EnterpriseAccountStatus
    revision: int
    credential_version: int
