"""组织/学术结构：学院、专业、班级与研究方向（SPEC §4 / T16）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.student import Student

# 嵌入维度：bge-m3（SPEC §2 本地嵌入）为 1024 维。research_directions / job_parses 共用。
EMBED_DIM = 1024


class College(Base, TimestampMixin):
    __tablename__ = "colleges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)

    majors: Mapped[list[Major]] = relationship(back_populates="college")
    academic_classes: Mapped[list[AcademicClass]] = relationship(back_populates="college")
    directions: Mapped[list[ResearchDirection]] = relationship(back_populates="college")


class Major(Base, TimestampMixin):
    __tablename__ = "majors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # code 用教育部本科专业目录代码（SPEC §4）
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 本科/硕士/博士（enums.DegreeLevel）
    level: Mapped[str] = mapped_column(String(8), nullable=False)

    college_id: Mapped[int] = mapped_column(ForeignKey("colleges.id"), nullable=False)
    college: Mapped[College] = relationship(back_populates="majors")
    academic_classes: Mapped[list[AcademicClass]] = relationship(back_populates="major")

    # 裁决#2：同一专业代码可在不同学院开设，唯一性按 (学院, 代码, 层次)
    __table_args__ = (
        UniqueConstraint("college_id", "code", "level", name="uq_major_college_code_level"),
    )


class AcademicClass(Base, TimestampMixin):
    """正式数据启用中的行政班级目录。"""

    __tablename__ = "academic_classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    cohort_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    college_id: Mapped[int] = mapped_column(
        ForeignKey("colleges.id", ondelete="RESTRICT"), nullable=False
    )
    major_id: Mapped[int] = mapped_column(
        ForeignKey("majors.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    college: Mapped[College] = relationship(back_populates="academic_classes")
    major: Mapped[Major] = relationship(back_populates="academic_classes")
    students: Mapped[list[Student]] = relationship(back_populates="academic_class")

    __table_args__ = (
        UniqueConstraint(
            "college_id",
            "major_id",
            "cohort_year",
            "code",
            name="uq_academic_class_scope_code",
        ),
        CheckConstraint(
            "cohort_year BETWEEN 2000 AND 2100",
            name="ck_academic_classes_cohort_year",
        ),
    )


class ResearchDirection(Base, TimestampMixin):
    __tablename__ = "research_directions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)

    college_id: Mapped[int] = mapped_column(ForeignKey("colleges.id"), nullable=False)
    college: Mapped[College] = relationship(back_populates="directions")
