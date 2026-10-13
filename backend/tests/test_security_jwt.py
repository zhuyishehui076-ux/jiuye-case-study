"""Pure HS256 entry-point regression tests; no database or HTTP calls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import sys

import jwt
import pytest

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token


class _HiddenToken(str):
    def __repr__(self) -> str:
        return "<synthetic JWT>"


def _signed_token_for_header(header: bytes) -> str:
    # Reuse a valid synthetic payload and sign the replacement header correctly.
    # Rejection must come from header parsing rather than a stale signature.
    valid = create_access_token("synthetic-user", "admin", None)
    payload = valid.split(".")[1]
    encoded_header = base64.urlsafe_b64encode(header).rstrip(b"=").decode("ascii")
    signing_input = (encoded_header + "." + payload).encode("ascii")
    signature = hmac.new(
        settings.jwt_secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return _HiddenToken(encoded_header + "." + payload + "." + encoded_signature)


def test_generated_hs256_token_roundtrip():
    token = _HiddenToken(create_access_token("synthetic-user", "admin", None))
    payload = decode_access_token(token)
    assert payload["sub"] == "synthetic-user"
    assert payload["role"] == "admin"


@pytest.mark.parametrize("header_kind", ["malformed_json", "deeply_nested_json"])
def test_bad_headers_are_rejected_without_parser_crash(header_kind):
    if header_kind == "malformed_json":
        header = b'{"alg":"HS256",'
    else:
        depth = sys.getrecursionlimit() + 128
        header = b'{"alg":"HS256","padding":' + b"[" * depth + b"0" + b"]" * depth + b"}"
    token = _signed_token_for_header(header)
    # A PyJWT rejection is handled by the auth layer; RecursionError is not.
    with pytest.raises(jwt.DecodeError):
        decode_access_token(token)
