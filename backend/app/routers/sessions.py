"""Session management + API keys + audit endpoints."""
from __future__ import annotations
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentUser, get_current_user, tenant_session
from ..models import ApiKey, AuditEvent, RefreshToken
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, AuditOut, SessionOut

router = APIRouter(tags=["sessions"])


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    session: AsyncSession = Depends(tenant_session),
    current: CurrentUser = Depends(get_current_user),
) -> list[SessionOut]:
    now = datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.user_id == current.id)
            .where(RefreshToken.revoked.is_(False))
            .where(RefreshToken.used.is_(False))
            .where(RefreshToken.expires_at > now)
            .order_by(RefreshToken.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [SessionOut.model_validate(r) for r in rows]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: uuid.UUID,
    session: AsyncSession = Depends(tenant_session),
    current: CurrentUser = Depends(get_current_user),
):
    row = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.id == session_id)
            .where(RefreshToken.user_id == current.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    row.revoked = True
    session.add(AuditEvent(
        tenant_id=current.tenant_id,
        actor_user_id=current.id,
        event_type="session.revoked",
        resource_type="refresh_token",
        resource_id=str(row.id),
        metadata_json={},
    ))


# ---------- API keys ----------
api_keys_router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _new_api_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, hash). Plaintext shown once only."""
    raw = f"evai_{secrets.token_urlsafe(32)}"
    return raw, raw[:12], hashlib.sha256(raw.encode()).hexdigest()


@api_keys_router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    session: AsyncSession = Depends(tenant_session),
    current: CurrentUser = Depends(get_current_user),
) -> list[ApiKeyOut]:
    rows = (
        await session.execute(
            select(ApiKey).where(ApiKey.user_id == current.id).order_by(ApiKey.created_at.desc())
        )
    ).scalars().all()
    return [ApiKeyOut.model_validate(r) for r in rows]


@api_keys_router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    payload: ApiKeyCreate,
    session: AsyncSession = Depends(tenant_session),
    current: CurrentUser = Depends(get_current_user),
) -> ApiKeyCreated:
    raw, prefix, hashed = _new_api_key()
    row = ApiKey(
        tenant_id=current.tenant_id,
        user_id=current.id,
        name=payload.name,
        prefix=prefix,
        key_hash=hashed,
    )
    session.add(row)
    session.add(AuditEvent(
        tenant_id=current.tenant_id,
        actor_user_id=current.id,
        event_type="api_key.created",
        resource_type="api_key",
        metadata_json={"name": payload.name},
    ))
    await session.flush()
    return ApiKeyCreated(
        id=row.id, name=row.name, prefix=row.prefix, plaintext_key=raw, created_at=row.created_at
    )


@api_keys_router.delete("/{api_key_id}", status_code=204)
async def revoke_api_key(
    api_key_id: uuid.UUID,
    session: AsyncSession = Depends(tenant_session),
    current: CurrentUser = Depends(get_current_user),
):
    row = (
        await session.execute(
            select(ApiKey).where(ApiKey.id == api_key_id).where(ApiKey.user_id == current.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    row.revoked = True
    session.add(AuditEvent(
        tenant_id=current.tenant_id,
        actor_user_id=current.id,
        event_type="api_key.revoked",
        resource_type="api_key",
        resource_id=str(row.id),
        metadata_json={},
    ))


# ---------- audit ----------
audit_router = APIRouter(prefix="/audit", tags=["audit"])


@audit_router.get("", response_model=list[AuditOut])
async def list_audit_events(
    session: AsyncSession = Depends(tenant_session),
    _current: CurrentUser = Depends(get_current_user),
) -> list[AuditOut]:
    rows = (
        await session.execute(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200))
    ).scalars().all()
    return [AuditOut.model_validate(r, from_attributes=True) for r in rows]
