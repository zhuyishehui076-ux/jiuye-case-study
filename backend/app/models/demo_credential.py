"""Hashed-only journal for recoverable Demo credential releases."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DemoCredentialRelease(Base, TimestampMixin):
    """A staged/active Demo credential set without any plaintext secret."""

    __tablename__ = "demo_credential_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    installation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    handoff_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    management_password_hashes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    verification_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    previous_release_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("demo_credential_releases.release_id", ondelete="RESTRICT"),
        nullable=True,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    student_binding_code_digest: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    student_binding_code_hash: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    enterprise_access_token_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise_access_tokens.id", ondelete="RESTRICT"),
        nullable=False,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('staged', 'verified', 'promoting', 'active', "
            "'superseded', 'aborted')",
            name="ck_demo_credential_releases_status",
        ),
        Index(
            "uq_demo_credential_releases_candidate",
            "installation_id",
            unique=True,
            postgresql_where=text(
                "status IN ('staged', 'verified', 'promoting')"
            ),
        ),
        Index(
            "uq_demo_credential_releases_active",
            "installation_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
