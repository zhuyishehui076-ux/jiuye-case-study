"""学生主档 students（SPEC §4）。

联系方式加密存储：contact_encrypted 存密文（core.crypto），明文不落库。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import RiskLevel


class Student(Base, TimestampMixin):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(4), nullable=True)

    college_id: Mapped[int] = mapped_column(ForeignKey("colleges.id"), nullable=False)
    major_id: Mapped[int] = mapped_column(ForeignKey("majors.id"), nullable=False)
    direction_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_directions.id"), nullable=True
    )
    # 兼容 T15 前已有数据保持可空；T16 正式学生模板在服务层要求填写。
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("academic_classes.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    degree_level: Mapped[str] = mapped_column(String(8), nullable=False)  # enums.DegreeLevel
    cohort_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 届别
    origin_place: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 生源地
    # 联系方式加密存储（SPEC §4）
    contact_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 去向状态（enums.EmploymentStatus），默认未落实
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="未落实", index=True)
    # 去向事实发生时间；与状态同时更新、回退，禁止在启用过程中丢失。
    status_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 预警等级（enums.RiskLevel），由 services/risk.py 写入
    risk_level: Mapped[str] = mapped_column(
        String(8), nullable=False, default=RiskLevel.GREEN.value, index=True
    )
    risk_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 困难群体标记（enums.KeyGroup），空=非困难群体（SPEC §4 字段名 is_key_group）
    is_key_group: Mapped[str | None] = mapped_column(String(16), nullable=True)

    college = relationship("College")
    major = relationship("Major")
    direction = relationship("ResearchDirection")
    academic_class = relationship("AcademicClass", back_populates="students")
