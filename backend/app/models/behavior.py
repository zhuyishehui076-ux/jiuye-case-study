"""学生行为与画像相关表（SPEC §4）：
assessments / activities / activity_attendance / job_actions / profiles。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Assessment(Base, TimestampMixin):
    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # enums.AssessmentType
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    term: Mapped[str] = mapped_column(String(16), nullable=False)

    student = relationship("Student")


class Activity(Base, TimestampMixin):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attendance: Mapped[list[ActivityAttendance]] = relationship(back_populates="activity")


class ActivityAttendance(Base, TimestampMixin):
    __tablename__ = "activity_attendance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # enums.AttendanceSource
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    activity: Mapped[Activity] = relationship(back_populates="attendance")
    student = relationship("Student")

    __table_args__ = (
        UniqueConstraint("student_id", "activity_id", name="uq_attendance_student_activity"),
    )


class JobAction(Base, TimestampMixin):
    __tablename__ = "job_actions"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            name="uq_job_actions_application_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(8), nullable=False)  # enums.JobActionType
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 目标单位/岗位
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_applications.id", ondelete="RESTRICT"),
        nullable=True,
    )

    student = relationship("Student")


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    term: Mapped[str] = mapped_column(String(16), nullable=False)
    six_dims_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    narrative_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 每个结论 → 数据来源（SPEC §5 画像研判证据溯源）
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # {model, prompt_ver, provider}
    llm_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    student = relationship("Student")

    # 按学期版本化：唯一键 (student_id, term)（SPEC §4）
    __table_args__ = (UniqueConstraint("student_id", "term", name="uq_profile_student_term"),)
