"""PostgreSQL-backed replay receipts for compound management writes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import OperationReceipt
from app.services.write_idempotency import (
    WriteOperationConflict,
    validate_write_operation_key,
    write_request_hash,
)


class OperationReceiptConflict(WriteOperationConflict):
    """One key was reused with a different actor or semantic payload."""


def claim_operation(
    db: Session,
    *,
    namespace: str,
    idempotency_key: str,
    actor: str,
    payload: Mapping[str, Any],
) -> tuple[OperationReceipt, bool]:
    key = validate_write_operation_key(idempotency_key)
    request_hash = write_request_hash(namespace, actor=actor, payload=payload)
    receipt = OperationReceipt(
        namespace=namespace,
        idempotency_key=key,
        request_hash=request_hash,
        result_json={},
        created_by=actor,
    )
    db.add(receipt)
    try:
        db.flush()
        return receipt, False
    except IntegrityError:
        # The claim is always the first mutation in its transaction. A
        # concurrent winner either commits (the unique insert then fails) or
        # rolls back (this insert succeeds), so a replay can be read safely.
        db.rollback()
        existing = db.execute(
            select(OperationReceipt).where(
                OperationReceipt.namespace == namespace,
                OperationReceipt.idempotency_key == key,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        if existing.request_hash != request_hash:
            raise OperationReceiptConflict(
                "Idempotency-Key was already used for a different request"
            ) from None
        return existing, True


def finish_operation(
    receipt: OperationReceipt,
    *,
    result: Mapping[str, Any],
) -> None:
    receipt.result_json = dict(result)


def get_operation_result(
    db: Session,
    *,
    namespace: str,
    idempotency_key: str,
    actor: str,
) -> dict[str, Any] | None:
    """Return one committed operation fact without exposing another actor's key."""

    key = validate_write_operation_key(idempotency_key)
    receipt = db.execute(
        select(OperationReceipt).where(
            OperationReceipt.namespace == namespace,
            OperationReceipt.idempotency_key == key,
            OperationReceipt.created_by == actor,
        )
    ).scalar_one_or_none()
    if receipt is None or not receipt.result_json:
        return None
    return dict(receipt.result_json)
