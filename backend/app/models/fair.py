"""Recruitment fairs and scene statistics models (SPEC §4)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Fair(Base, TimestampMixin):
    __tablename__ = "fairs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_fairs_idempotency_key"),
        UniqueConstraint(
            "student_checkin_code_digest",
            name="uq_fairs_student_checkin_code_digest",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL AND request_hash IS NULL) OR "
            "(idempotency_key IS NOT NULL AND request_hash IS NOT NULL)",
            name="ck_fairs_operation_pair",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 8 AND 96",
            name="ck_fairs_idempotency_key_length",
        ),
        CheckConstraint(
            "request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_fairs_request_hash",
        ),
        CheckConstraint(
            "(student_checkin_code_digest IS NULL AND "
            "student_checkin_code_hash IS NULL AND "
            "student_checkin_code_issued_at IS NULL) OR "
            "(student_checkin_code_digest IS NOT NULL AND "
            "student_checkin_code_hash IS NOT NULL AND "
            "student_checkin_code_issued_at IS NOT NULL)",
            name="ck_fairs_student_checkin_code_pair",
        ),
        CheckConstraint(
            "student_checkin_code_digest IS NULL OR "
            "student_checkin_code_digest ~ '^[0-9a-f]{64}$'",
            name="ck_fairs_student_checkin_code_digest",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    fair_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="综合招聘会", index=True
    )
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="计划中", index=True
    )
    registration_opens_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkin_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkin_ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    student_checkin_code_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    student_checkin_code_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    student_checkin_code_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    companies: Mapped[list[FairCompany]] = relationship(
        back_populates="fair", cascade="all, delete-orphan"
    )


class FairCompany(Base, TimestampMixin):
    __tablename__ = "fair_companies"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_fair_companies_idempotency_key",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL AND request_hash IS NULL) OR "
            "(idempotency_key IS NOT NULL AND request_hash IS NOT NULL)",
            name="ck_fair_companies_operation_pair",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 8 AND 96",
            name="ck_fair_companies_idempotency_key_length",
        ),
        CheckConstraint(
            "request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_fair_companies_request_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fair_id: Mapped[int] = mapped_column(ForeignKey("fairs.id"), nullable=False, index=True)
    enterprise_organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("enterprise_organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    company: Mapped[str] = mapped_column(String(128), nullable=False)
    booth: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="已报名")
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    fair: Mapped[Fair] = relationship(back_populates="companies")


class FairCheckin(Base, TimestampMixin):
    """Named check-ins and anonymous counts share one table as separate rows."""

    __tablename__ = "fair_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fair_id: Mapped[int] = mapped_column(ForeignKey("fairs.id"), nullable=False, index=True)
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id"), nullable=True, index=True
    )
    anonymous_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="校方登记"
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(96), nullable=True, unique=True
    )
    corrected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("fair_id", "student_id", name="uq_fair_checkin_student"),
    )


class FairEnterpriseCheckin(Base, TimestampMixin):
    """企业代表签到；仅保存代表显示名，不保存联系方式。"""

    __tablename__ = "fair_enterprise_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fair_company_id: Mapped[int] = mapped_column(
        ForeignKey("fair_companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    representative_name: Mapped[str] = mapped_column(String(64), nullable=False)
    gate_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(96), nullable=True, unique=True
    )
    corrected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "fair_company_id",
            "representative_name",
            name="uq_fair_enterprise_representative",
        ),
    )


class FairSnapshot(Base, TimestampMixin):
    """场次结束时冻结的汇总快照；补录后保留审计并重算快照。"""

    __tablename__ = "fair_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fair_id: Mapped[int] = mapped_column(
        ForeignKey("fairs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class SceneMetric(Base, TimestampMixin):
    """M6 aggregate-only metrics; no individual trajectory storage."""

    __tablename__ = "scene_metrics"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_scene_metrics_idempotency_key",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL AND request_hash IS NULL) OR "
            "(idempotency_key IS NOT NULL AND request_hash IS NOT NULL)",
            name="ck_scene_metrics_operation_pair",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_scene_metrics_idempotency_key_length",
        ),
        CheckConstraint(
            "request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_scene_metrics_request_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fair_id: Mapped[int] = mapped_column(ForeignKey("fairs.id"), nullable=False, index=True)
    zone: Mapped[str] = mapped_column(String(64), nullable=False)
    ts_bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    headcount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dwell_avg_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
