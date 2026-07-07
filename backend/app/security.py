"""JWT (RS256) issuance/verification and Argon2id password hashing.

Access tokens are stateless JWTs (RS256), short-lived (default 10 min).
Refresh tokens are OPAQUE random 32-byte strings; their SHA-256 hash is
stored server-side in ``refresh_tokens`` so rotation, reuse detection, and
logout can be enforced. Refresh tokens live in an httpOnly Secure cookie.
"""
from __future__ import annotations
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import jwt as _jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import get_settings

_settings = get_settings()
_ph = PasswordHasher()  # argon2id defaults are secure


# ---------- passwords ----------
def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        _ph.verify(hashed, pw)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ---------- access tokens ----------
@dataclass
class AccessTokenClaims:
    sub: str
    tenant_id: str
    role: str
    jti: str
    exp: int


def issue_access_token(user_id: str, tenant_id: str, role: str) -> tuple[str, AccessTokenClaims]:
    now = int(time.time())
    claims = AccessTokenClaims(
        sub=str(user_id),
        tenant_id=str(tenant_id),
        role=role,
        jti=uuid.uuid4().hex,
        exp=now + _settings.jwt_access_ttl,
    )
    payload = {
        "sub": claims.sub,
        "tenant_id": claims.tenant_id,
        "role": claims.role,
        "jti": claims.jti,
        "iat": now,
        "exp": claims.exp,
        "typ": "access",
    }
    token = _jwt.encode(payload, _settings.jwt_private_key, algorithm="RS256")
    return token, claims


def verify_access_token(token: str) -> Optional[AccessTokenClaims]:
    try:
        payload = _jwt.decode(token, _settings.jwt_public_key, algorithms=["RS256"])
        if payload.get("typ") != "access":
            return None
        return AccessTokenClaims(
            sub=payload["sub"],
            tenant_id=payload["tenant_id"],
            role=payload["role"],
            jti=payload["jti"],
            exp=int(payload["exp"]),
        )
    except _jwt.PyJWTError:
        return None


# ---------- refresh tokens (opaque) ----------
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
