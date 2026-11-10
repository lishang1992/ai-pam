"""身份认证：口令哈希 + 轻量签名令牌（纯标准库，Windows 免编译依赖）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

from ..settings import SECRET_KEY, TOKEN_TTL_SECONDS

PBKDF2_ROUNDS = 120_000


def hash_password(password: str, salt: Optional[str] = None) -> Dict[str, str]:
    salt = salt or base64.b16encode(os.urandom(16)).decode()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ROUNDS
    )
    return {"salt": salt, "hash": base64.b16encode(digest).decode()}


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    calc = hash_password(password, salt)["hash"]
    return hmac.compare_digest(calc, expected_hash)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def issue_token(payload: Dict[str, Any], ttl_seconds: Optional[int] = None) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + int(ttl_seconds or TOKEN_TTL_SECONDS)
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signed = _b64e(raw)
    sig = hmac.new(SECRET_KEY.encode("utf-8"), signed.encode("utf-8"), hashlib.sha256).digest()
    return signed + "." + _b64e(sig)


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        signed, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(
        SECRET_KEY.encode("utf-8"), signed.encode("utf-8"), hashlib.sha256
    ).digest()
    try:
        given = _b64d(sig)
    except Exception:
        return None
    if not hmac.compare_digest(expected, given):
        return None
    try:
        payload = json.loads(_b64d(signed).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload

