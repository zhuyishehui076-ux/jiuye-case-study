"""T10 M7 visit expansion schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class VisitCreateRequest(BaseModel):
    company: str = Field(min_length=1, max_length=128)
    visitors: str | None = Field(default=None, max_length=128)
    visited_on: date
    promised_jobs: int = Field(ge=0)
    follow_status: str | None = Field(default=None, max_length=32)
    note: str | None = None


class VisitRow(BaseModel):
    id: int
    company: str
    visitors: str | None
    visited_on: date
    promised_jobs: int
    company_visit_count: int
    company_promised_jobs: int
    submitted_jds: int
    hires: int
    jd_conversion_rate: float
    hire_conversion_rate: float
    follow_status: str | None
    note: str | None


class VisitCreateResponse(VisitRow):
    replayed: bool


class VisitDashboardSummary(BaseModel):
    visit_count: int
    promised_jobs: int
    submitted_jds: int
    hires: int
    jd_conversion_rate: float
    hire_conversion_rate: float


class VisitDashboardResponse(BaseModel):
    summary: VisitDashboardSummary
    rows: list[VisitRow]
