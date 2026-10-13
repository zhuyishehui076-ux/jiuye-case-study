"""Import center run and batch state for SPEC §7 T16."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class ImportRun(Base, TimestampMixin):
    """A persistent five-template production enablement session."""

    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cohort_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    business_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", index=True)
    template_version: Mapped[str] = mapped_column(String(16), nullable=False, default="t16.v1")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rollback_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    batches: Mapped[list[ImportBatch]] = relationship(back_populates="run")

    __table_args__ = (
        CheckConstraint(
            "cohort_year BETWEEN 2000 AND 2100",
            name="ck_import_runs_cohort_year",
        ),
        CheckConstraint(
            "status IN ('draft', 'needs_correction', 'ready', 'importing', "
            "'imported', 'rolling_back', 'rolled_back')",
            name="ck_import_runs_status",
        ),
        CheckConstraint("revision >= 0", name="ck_import_runs_revision"),
    )


class ImportBatch(Base, TimestampMixin):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # T7 legacy batches remain valid with no run; T16 batches always bind a run.
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    original_filename: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="validated", index=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    staged_rows: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    inserted_refs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    template_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_rows: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    mapping_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    correction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    supersedes_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[ImportRun | None] = relationship(back_populates="batches")
    target_claims: Mapped[list[ImportTargetClaim]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )
    supersedes: Mapped[ImportBatch | None] = relationship(
        remote_side="ImportBatch.id",
        foreign_keys=[supersedes_batch_id],
        back_populates="superseded_by",
    )
    superseded_by: Mapped[list[ImportBatch]] = relationship(
        foreign_keys=[supersedes_batch_id],
        back_populates="supersedes",
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "target_kind",
            "source_sha256",
            name="uq_import_batches_run_kind_sha256",
        ),
        CheckConstraint(
            "status IN ('validated', 'failed', 'draft', 'uploaded', "
            "'needs_correction', 'ready', 'importing', 'imported', "
            "'rolling_back', 'rolled_back', 'superseded')",
            name="ck_import_batches_status",
        ),
        CheckConstraint("row_count >= 0", name="ck_import_batches_row_count"),
        CheckConstraint("error_count >= 0", name="ck_import_batches_error_count"),
        CheckConstraint(
            "correction_count >= 0",
            name="ck_import_batches_correction_count",
        ),
        CheckConstraint(
            "source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_import_batches_source_sha256",
        ),
        Index(
            "ix_import_batches_run_kind_status",
            "run_id",
            "target_kind",
            "status",
        ),
    )


class ImportTargetClaim(Base):
    """A privacy-safe dependency claim on a business row touched by a T16 batch."""

    __tablename__ = "import_target_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    field_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped[ImportBatch] = relationship(back_populates="target_claims")

    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "resource_kind",
            "field_scope",
            "target_id",
            name="uq_import_target_claim_batch_scope_target",
        ),
        UniqueConstraint(
            "resource_kind",
            "target_id",
            "target_version",
            name="uq_import_target_claim_target_version",
        ),
        CheckConstraint(
            "resource_kind IN ('college', 'major', 'class', 'student')",
            name="ck_import_target_claim_resource_kind",
        ),
        CheckConstraint(
            "field_scope IN ('record', 'student_profile', 'student_outcome')",
            name="ck_import_target_claim_field_scope",
        ),
        CheckConstraint(
            "mode IN ('inserted', 'updated', 'unchanged')",
            name="ck_import_target_claim_mode",
        ),
        CheckConstraint("target_id > 0", name="ck_import_target_claim_target_id"),
        CheckConstraint(
            "target_version > 0",
            name="ck_import_target_claim_target_version",
        ),
        Index(
            "ix_import_target_claim_active_target",
            "resource_kind",
            "target_id",
            "released_at",
        ),
    )
