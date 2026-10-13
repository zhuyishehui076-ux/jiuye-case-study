"""一生一策帮扶台账（SPEC §4）：assist_cases / assist_records。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class AssistCase(Base, TimestampMixin):
    __tablename__ = "assist_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="进行中", index=True
    )  # enums.AssistStatus

    student = relationship("Student")
    records: Mapped[list[AssistRecord]] = relationship(back_populates="case")


class AssistRecord(Base, TimestampMixin):
    __tablename__ = "assist_records"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_assist_records_idempotency_key",
        ),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_assist_records_idempotency_key_length",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_assist_records_request_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("assist_cases.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)  # enums.AssistActionType
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    case: Mapped[AssistCase] = relationship(back_populates="records")
