"""Object storage wrapper: uploads ciphertext blobs to Emergent object storage.

Objects are always encrypted BEFORE reaching the storage service. The
storage service is treated as untrusted — nonces and auth tags are kept
alongside the object *in Postgres*, not in the object metadata.
"""
from __future__ import annotations
import logging
import os
import uuid
from typing import Optional

import requests

from .config import get_settings

logger = logging.getLogger(__name__)

_STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
_storage_key: Optional[str] = None


class StorageError(Exception):
    pass


def _headers() -> dict:
    return {"X-Storage-Key": _get_key()}


def _get_key() -> str:
    global _storage_key
    if _storage_key:
        return _storage_key
    settings = get_settings()
    if not settings.emergent_llm_key:
        raise StorageError("EMERGENT_LLM_KEY not configured")
    resp = requests.post(
        f"{_STORAGE_URL}/init",
        json={"emergent_key": settings.emergent_llm_key},
        timeout=30,
    )
    resp.raise_for_status()
    _storage_key = resp.json()["storage_key"]
    return _storage_key


def build_object_key(tenant_id: str, dataset_id: str) -> str:
    app = get_settings().app_name
    return f"{app}/tenants/{tenant_id}/datasets/{dataset_id}/{uuid.uuid4().hex}.enc"


def put_object(path: str, ciphertext: bytes) -> dict:
    resp = requests.put(
        f"{_STORAGE_URL}/objects/{path}",
        headers={**_headers(), "Content-Type": "application/octet-stream"},
        data=ciphertext,
        timeout=180,
    )
    if resp.status_code == 403:
        # storage_key expired; re-init once and retry
        global _storage_key
        _storage_key = None
        resp = requests.put(
            f"{_STORAGE_URL}/objects/{path}",
            headers={**_headers(), "Content-Type": "application/octet-stream"},
            data=ciphertext,
            timeout=180,
        )
    resp.raise_for_status()
    return resp.json()


def get_object(path: str) -> bytes:
    resp = requests.get(f"{_STORAGE_URL}/objects/{path}", headers=_headers(), timeout=120)
    if resp.status_code == 403:
        global _storage_key
        _storage_key = None
        resp = requests.get(f"{_STORAGE_URL}/objects/{path}", headers=_headers(), timeout=120)
    resp.raise_for_status()
    return resp.content


def init_storage_or_log() -> None:
    """Startup hook. Non-fatal: logs but does not crash if storage is down."""
    try:
        _get_key()
        logger.info("Object storage initialized.")
    except Exception as e:  # noqa: BLE001
        logger.warning("Object storage init deferred (will retry lazily): %s", e)
