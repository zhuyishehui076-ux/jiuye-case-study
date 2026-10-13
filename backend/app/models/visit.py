"""M7 访企拓岗（SPEC §4）：company_visits。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CompanyVisit(Base, TimestampMixin):
    __tablename__ = "company_visits"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_company_visits_idempotency_key",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL AND request_hash IS NULL) OR "
            "(idempotency_key IS NOT NULL AND request_hash IS NOT NULL)",
            name="ck_company_visits_operation_pair",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_company_visits_idempotency_key_length",
        ),
        CheckConstraint(
            "request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_company_visits_request_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company: Mapped[str] = mapped_column(String(128), nullable=False)
    visitors: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 走访人
    visited_on: Mapped[date] = mapped_column(Date, nullable=False)
    # 岗位承诺（承诺岗位数等），SPEC §4 commitments_json
    commitments_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    follow_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
