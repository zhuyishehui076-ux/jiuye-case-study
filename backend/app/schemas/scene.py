"""Schemas for T9 scene statistics."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SceneFairOption(BaseModel):
    id: int
    name: str
    fair_type: str
    held_at: datetime | None
    location: str | None


class SceneMetricPush(BaseModel):
    zone: str = Field(min_length=1)
    ts: datetime
    headcount: int = Field(ge=0)
    dwell_avg_s: float | None = Field(default=None, ge=0)


class SceneMetricResponse(BaseModel):
    id: int
    fair_id: int
    zone: str
    ts_bucket: datetime
    headcount: int
    dwell_avg_s: float | None
    replayed: bool


class SceneIdentityReport(BaseModel):
    checked_in_students: int
    named_checkins: int
    anonymous_count: int
    attendance_rate: float
    college_distribution: dict[str, int]
    key_group_checked_in: int


class SceneEnterpriseReport(BaseModel):
    registered_companies: int = Field(ge=0)
    approved_companies: int = Field(ge=0)
    checked_in_companies: int = Field(ge=0)
    representative_checkins: int = Field(ge=0)
    assigned_booths: int = Field(ge=0)


class SceneFairComparison(BaseModel):
    fair_id: int
    fair_name: str
    fair_type: str
    held_at: datetime | None
    attendance_rate: float
    total_headcount: int


class SceneReport(BaseModel):
    fair_id: int
    fair_name: str
    fair_type: str
    identity: SceneIdentityReport
    enterprise: SceneEnterpriseReport
    total_headcount: int
    avg_dwell_s: float | None
    zone_heat: dict[str, int]
    time_buckets: dict[str, int]
    same_type_comparison: list[SceneFairComparison]


class MockSeedResponse(BaseModel):
    seeded_fairs: int
    fair_ids: list[int]
