"""岗位解析与匹配（SPEC §4）：jobs / job_parses / matches。"""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.org import EMBED_DIM


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enterprise_organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("enterprise_organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    company: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # enums.JobSource
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    parse: Mapped[JobParse | None] = relationship(
        back_populates="job", uselist=False
    )


class JobParse(Base, TimestampMixin):
    __tablename__ = "job_parses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # job_id 1:1（SPEC §4）
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id"), nullable=False, unique=True
    )
    degree_req: Mapped[str | None] = mapped_column(String(8), nullable=True)
    major_codes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    direction_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    location: Mapped[str | None] = mapped_column(String(64), nullable=True)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    llm_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    # 兜底人工复核队列（T2 §5）：低置信/解析失败置 True，进人工复核
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 人工复核事实的单调版本。每次复核在行锁内 compare-and-swap，
    # 防止两个浏览器标签页互相静默覆盖。
    review_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    job: Mapped[Job] = relationship(back_populates="parse")

    __table_args__ = (
        CheckConstraint(
            "review_revision >= 1",
            name="ck_job_parses_review_revision_positive",
        ),
    )


class Match(Base, TimestampMixin):
    """job × student / job × major 两级匹配（SPEC §4）。

    student_id 与 major_id 二选一：学生级直推用 student_id，专业级候选用 major_id。
    """

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id"), nullable=True, index=True
    )
    major_id: Mapped[int | None] = mapped_column(
        ForeignKey("majors.id"), nullable=True, index=True
    )
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)  # 0-100
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)  # enums.MatchFeedback
    feedback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("job_id", "student_id", name="uq_match_job_student"),
        UniqueConstraint("job_id", "major_id", name="uq_match_job_major"),
        CheckConstraint(
            "(feedback IS NULL AND feedback_at IS NULL) OR "
            "(feedback IS NOT NULL AND feedback_at IS NOT NULL)",
            name="ck_matches_feedback_timestamp_pair",
        ),
    )
