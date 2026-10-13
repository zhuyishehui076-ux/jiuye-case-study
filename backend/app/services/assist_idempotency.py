"""Canonical idempotency helpers for the assist-record write boundary."""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5


class InvalidAssistIdempotencyKey(ValueError):
    """Raised when a client key is not a canonical random UUIDv4."""


def validate_assist_idempotency_key(value: str) -> str:
    """Return a canonical UUIDv4 key or fail closed.

    Requiring a random UUID prevents predictable cross-request collisions while
    keeping the value safe to persist as non-secret request metadata.
    """

    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise InvalidAssistIdempotencyKey(
            "Idempotency-Key must be a canonical UUIDv4"
        ) from exc
    canonical = str(parsed)
    if parsed.version != 4 or value != canonical:
        raise InvalidAssistIdempotencyKey(
            "Idempotency-Key must be a canonical UUIDv4"
        )
    return canonical


def assist_record_request_hash(
    *,
    case_id: int,
    action_type: str,
    note: str | None,
    actor: str,
) -> str:
    """Hash every semantic input, including the authenticated actor and case."""

    canonical = json.dumps(
        {
            "action_type": action_type,
            "actor": actor,
            "case_id": case_id,
            "note": note,
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deterministic_assist_idempotency_key(identity: str) -> str:
    """Create stable non-client keys for seeds and direct test fixtures."""

    return str(uuid5(NAMESPACE_URL, f"jiuye:assist-record:{identity}"))
