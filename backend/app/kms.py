"""Envelope encryption interface.

Master KEK is loaded from the secrets abstraction (env-backed in dev,
1:1 swappable for AWS KMS / Vault by replacing ``_load_kek``).

Per-tenant DEK is generated at tenant creation, wrapped with the KEK
using AES-256-GCM, and stored as ``tenants.wrapped_dek``. Plaintext DEK
never touches disk; it is unwrapped in memory only when a tenant's file
must be encrypted/decrypted.

Per-file encryption:
- Fresh 96-bit nonce (``os.urandom(12)``) per file.
- AES-256-GCM; auth tag is emitted separately for storage alongside
  the ciphertext record.
"""
from __future__ import annotations
import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_KEK_VERSION_CURRENT = 1


def _load_kek(version: int = _KEK_VERSION_CURRENT) -> bytes:
    """Load master key for the given version. Dev: env var. Prod: KMS."""
    if version != _KEK_VERSION_CURRENT:
        # Future: look up historic key material by version for re-wrap ops.
        raise ValueError(f"Unknown KEK version {version}")
    b64 = get_settings().master_kek_b64
    key = base64.b64decode(b64)
    if len(key) != 32:
        raise ValueError("MASTER_KEK_B64 must decode to 32 bytes (AES-256)")
    return key


# ---------- DEK wrap/unwrap ----------
@dataclass
class WrappedDEK:
    kek_version: int
    nonce_b64: str
    ciphertext_b64: str

    def to_json(self) -> dict:
        return {
            "kek_version": self.kek_version,
            "nonce": self.nonce_b64,
            "ciphertext": self.ciphertext_b64,
        }

    @classmethod
    def from_json(cls, d: dict) -> "WrappedDEK":
        return cls(
            kek_version=int(d["kek_version"]),
            nonce_b64=d["nonce"],
            ciphertext_b64=d["ciphertext"],
        )


def create_tenant_dek() -> tuple[bytes, WrappedDEK]:
    """Return (plaintext DEK, wrapped DEK). Caller stores only wrapped."""
    dek = os.urandom(32)  # AES-256 DEK
    kek = _load_kek(_KEK_VERSION_CURRENT)
    nonce = os.urandom(12)
    aes = AESGCM(kek)
    ct = aes.encrypt(nonce, dek, associated_data=b"tenant-dek")
    return dek, WrappedDEK(
        kek_version=_KEK_VERSION_CURRENT,
        nonce_b64=base64.b64encode(nonce).decode(),
        ciphertext_b64=base64.b64encode(ct).decode(),
    )


def unwrap_dek(wrapped: WrappedDEK) -> bytes:
    kek = _load_kek(wrapped.kek_version)
    nonce = base64.b64decode(wrapped.nonce_b64)
    ct = base64.b64decode(wrapped.ciphertext_b64)
    aes = AESGCM(kek)
    return aes.decrypt(nonce, ct, associated_data=b"tenant-dek")


# ---------- File data encrypt/decrypt ----------
@dataclass
class EncryptedBlob:
    nonce: bytes  # 12 bytes
    ciphertext: bytes  # includes GCM tag suffix (cryptography lib format)


def encrypt_with_dek(dek: bytes, plaintext: bytes, aad: bytes = b"") -> EncryptedBlob:
    nonce = os.urandom(12)
    aes = AESGCM(dek)
    ct = aes.encrypt(nonce, plaintext, associated_data=aad)
    return EncryptedBlob(nonce=nonce, ciphertext=ct)


def decrypt_with_dek(dek: bytes, blob: EncryptedBlob, aad: bytes = b"") -> bytes:
    aes = AESGCM(dek)
    return aes.decrypt(blob.nonce, blob.ciphertext, associated_data=aad)
