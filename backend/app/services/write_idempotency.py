"""Shared helpers for replay-safe management writes.

An operation key is non-secret request metadata.  It is intentionally a
canonical UUIDv4 so clients cannot accidentally reuse a human-readable value
across unrelated writes.  The request hash covers every semantic input and
the authenticated actor; a replay is valid only when both key and payload
match.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID


class InvalidWriteOperationKey(ValueError):
    """Raised when a write does not carry a canonical UUIDv4 operation key."""


class WriteOperationConflict(ValueError):
    """Raised when one operation key is reused for a different request."""


def validate_write_operation_key(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise InvalidWriteOperationKey(
            "Idempotency-Key must be a canonical UUIDv4"
        ) from exc
    canonical = str(parsed)
    if parsed.version != 4 or value != canonical:
        raise InvalidWriteOperationKey(
            "Idempotency-Key must be a canonical UUIDv4"
        )
    return canonical


def write_request_hash(
    namespace: str,
    *,
    actor: str,
    payload: Mapping[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "actor": actor,
            "namespace": namespace,
            "payload": payload,
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
