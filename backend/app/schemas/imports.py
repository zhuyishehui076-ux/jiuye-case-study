"""Schemas for T7 import center."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, StrictStr


class ImportErrorItem(BaseModel):
    row_number: int
    code: str
    reason: str
    field: str | None = None
    value: str | None = None
    sheet: str = "数据"
    column: str | None = None
    masked_value: str | None = None
    suggestion: str | None = None
    editable: bool = False
    batch_id: int | None = None
    kind: str | None = None


class ImportBatchResponse(BaseModel):
    batch_id: int
    target_kind: str
    status: str
    valid: bool
    row_count: int
    error_count: int
    error_row_count: int = 0
    errors: list[ImportErrorItem]
    title: str | None = None
    filename: str | None = None
    template_version: str | None = None
    file_sha256: str | None = None
    inserted_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    correction_count: int = 0


class ImportTemplateResponse(BaseModel):
    kind: str
    title: str
    filename: str
    schema_version: str | None = None
    order: int | None = None
    required: bool = False


class ImportConfirmResponse(BaseModel):
    batch_id: int
    status: str
    inserted_count: int


class ImportRollbackResponse(BaseModel):
    batch_id: int
    status: str
    rolled_back_count: int


class ImportRunCreateRequest(BaseModel):
    cohort_year: int
    business_note: str | None = None


class ImportRunCorrectionRequest(BaseModel):
    column: str
    value: StrictStr | None = None
    reason: str
    revision: int = Field(ge=1)


class ImportRunConfirmRequest(BaseModel):
    confirmation: Literal["确认导入并启用"]
    revision: int


class ImportRunRollbackRequest(BaseModel):
    confirmation: Literal["撤销本次启用"]
    revision: int
    reason: str


class ImportRunReconciliation(BaseModel):
    source: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    business_counts: dict[str, int] = Field(default_factory=dict)


class ImportRunListItem(BaseModel):
    id: int
    status: str
    cohort_year: int
    business_note: str | None = None
    template_version: str
    revision: int
    created_by: str | None = None
    created_at: datetime
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    rolled_back_by: str | None = None
    rolled_back_at: datetime | None = None
    rollback_reason: str | None = None


class ImportRunEvidence(BaseModel):
    id: int
    event: str
    label: str
    actor_id: int | None = None
    actor: str | None = None
    actor_role: str | None = None
    source_channel: str | None = None
    correlation_id: str | None = None
    batch_ids: list[int] = Field(default_factory=list)
    at: datetime
    detail: str | None = None


class ImportRunResponse(BaseModel):
    id: int
    status: str
    cohort_year: int
    business_note: str | None = None
    template_version: str
    revision: int
    created_by: str | None = None
    created_at: datetime
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    rolled_back_by: str | None = None
    rolled_back_at: datetime | None = None
    rollback_reason: str | None = None
    missing_kinds: list[str] = Field(default_factory=list)
    batches: list[ImportBatchResponse] = Field(default_factory=list)
    errors: list[ImportErrorItem] = Field(default_factory=list)
    reconciliation: ImportRunReconciliation = Field(default_factory=ImportRunReconciliation)
    evidence: list[ImportRunEvidence] = Field(default_factory=list)
    evidence_total: int = 0


class ImportIssuePage(BaseModel):
    items: list[ImportErrorItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    codes: list[str] = Field(default_factory=list)


class ImportEvidencePage(BaseModel):
    items: list[ImportRunEvidence] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class ImportBatchHistoryItem(BaseModel):
    batch_id: int
    target_kind: str
    title: str
    status: str
    filename: str | None = None
    template_version: str | None = None
    file_sha256: str | None = None
    row_count: int = 0
    error_count: int = 0
    correction_count: int = 0
    supersedes_batch_id: int | None = None
    created_by: str | None = None
    created_at: datetime
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    rolled_back_by: str | None = None
    rolled_back_at: datetime | None = None


class ImportBatchHistoryPage(BaseModel):
    items: list[ImportBatchHistoryItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class DemoScenarioResponse(BaseModel):
    id: str
    title: str
    description: str
    school_name: str
    cohort_year: int
    as_of_date: datetime
    counts: dict[str, int]
    quality_checks: list[str]
    active: bool
    preflight_status: str
    preflight_source_rows: int
    preflight_inserted_count: int
    preflight_updated_count: int
    preflight_skipped_count: int
    preflight_error_count: int
    preflight_issues: list[ImportErrorItem]
    can_activate: bool
    blocked_reason: str | None = None


class DemoScenarioActivateResponse(BaseModel):
    id: str
    status: str
    counts: dict[str, int]


class DemoScenarioClearResponse(BaseModel):
    id: str
    status: str
    cleared_students: int


class DemoScenarioResetRequest(BaseModel):
    confirmation: Literal["重新生成演示数据"]


class DemoScenarioClearRequest(BaseModel):
    confirmation: Literal["恢复空白状态"]
