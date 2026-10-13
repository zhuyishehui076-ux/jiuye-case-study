"""字段级对称加密（SPEC §4：联系方式等敏感字段加密存储）。

用 Fernet（AES128-CBC + HMAC）。密钥来源：
- 显式 FIELD_ENCRYPTION_KEY（生产 local 档由 install.sh 下发独立密钥，推荐）；
- 未配置时由 JWT_SECRET 派生（开发/演示可用，报告已标注为未决的密钥管理项）。

明文永不落库；读回需经 decrypt_field（供后续授权接口使用）。
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    raw = settings.field_encryption_key.strip()
    if raw:
        # 允许直接给标准 Fernet key，或给任意字符串（派生）
        try:
            return Fernet(raw.encode())
        except (ValueError, TypeError):
            seed = raw.encode()
    else:
        seed = f"field-enc::{settings.jwt_secret}".encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(key)


def encrypt_field(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return None
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_field(ciphertext: str | None) -> str | None:
    if ciphertext is None or ciphertext == "":
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None
