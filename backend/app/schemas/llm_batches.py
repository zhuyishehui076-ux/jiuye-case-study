"""T12 PostgreSQL LLM batch queue schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LLMBatchItemResponse(BaseModel):
    id: int
    target_id: int
    status: str
    result_id: int | None
    error_text: str | None
    started_at: datetime | None
    completed_at: datetime | None


class LLMBatchTaskResponse(BaseModel):
    id: int
    task_type: str
    status: str
    term: str | None
    total_items: int
    completed_items: int
    failed_items: int
    provider_name: str | None
    model_name: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error_summary: str | None
    items: list[LLMBatchItemResponse] = Field(default_factory=list)


class RunPendingResponse(BaseModel):
    status: str
    task_ids: list[int]
