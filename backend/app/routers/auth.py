"""Auth endpoints: register, login, refresh (rotation + reuse detection), logout, me."""
from __future__ import annotations
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update

from ..config import get_settings
from ..db import app_session, auth_session
from ..deps import CurrentUser, get_current_user, request_meta
from ..kms import create_tenant_dek
from ..models import AuditEvent, RefreshToken, Tenant, User
from ..schemas import LoginRequest, LoginResponse, MeResponse, RegisterRequest, TenantOut, UserOut
from ..security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
_settings = get_settings()

_REFRESH_COOKIE_NAME = "evai_rt"
_REFRESH_COOKIE_PATH = "/api/auth"


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return (s or "tenant")[:60]


def _set_refresh_cookie(resp: Response, raw_token: str, max_age: int) -> None:
    resp.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        secure=_settings.cookie_secure,
        samesite="strict",
        path=_REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(resp: Response) -> None:
    resp.delete_cookie(key=_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH)


async def _mint_refresh(
    session,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, RefreshToken]:
    raw = generate_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        tenant_id=tenant_id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_refresh_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=_settings.jwt_refresh_ttl),
        ip=ip,
        user_agent=user_agent,
    )
    session.add(row)
    await session.flush()
    return raw, row


@router.post("/register", response_model=LoginResponse, status_code=201)
async def register(payload: RegisterRequest, request: Request, response: Response) -> LoginResponse:
    meta = request_meta(request)
    slug_base = _slugify(payload.tenant_name)

    async with auth_session() as session:
        # Check email uniqueness across all tenants.
        existing = (
            await session.execute(select(User).where(User.email == payload.email))
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Email already registered")

        # Ensure unique slug (retry with suffix if needed).
        slug = slug_base
        for i in range(5):
            hit = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
            if hit is None:
                break
            slug = f"{slug_base}-{uuid.uuid4().hex[:6]}"
        else:
            raise HTTPException(status_code=500, detail="Failed to allocate tenant slug")

        _plaintext_dek, wrapped = create_tenant_dek()
        tenant = Tenant(name=payload.tenant_name, slug=slug, wrapped_dek=wrapped.to_json(), kek_version=1)
        session.add(tenant)
        await session.flush()

        user = User(
            tenant_id=tenant.id,
            email=payload.email,
            password_hash=hash_password(payload.password),
            role="admin",
            is_active=True,
        )
        session.add(user)
        await session.flush()

        raw, _rt = await _mint_refresh(
            session,
            user_id=user.id,
            tenant_id=tenant.id,
            ip=meta["ip"],
            user_agent=meta["user_agent"],
        )
        session.add(AuditEvent(
            tenant_id=tenant.id,
            actor_user_id=user.id,
            event_type="tenant.created",
            resource_type="tenant",
            resource_id=str(tenant.id),
            ip=meta["ip"],
            user_agent=meta["user_agent"],
            metadata_json={"email": payload.email},
        ))
        access, claims = issue_access_token(str(user.id), str(tenant.id), user.role)
        _set_refresh_cookie(response, raw, _settings.jwt_refresh_ttl)
    # Seed the demo dataset for this new tenant so a first-time viewer
    # immediately sees the reject/retry story on the datasets list.
    # Done AFTER the transaction commits (auth_session context exited).
    try:
        from ..demo_seed import _seed_for_tenant  # noqa: WPS433 (local import to avoid cycles)
        async with auth_session() as _s:
            await _seed_for_tenant(_s, tenant.id)
    except Exception:
        logging.getLogger(__name__).exception(
            "demo seed on register failed (non-fatal) for tenant %s", tenant.id,
        )
    return LoginResponse(
        access_token=access,
        expires_in=_settings.jwt_access_ttl,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, response: Response) -> LoginResponse:
    meta = request_meta(request)
    async with auth_session() as session:
        user = (
            await session.execute(select(User).where(User.email == payload.email))
        ).scalar_one_or_none()
        # Constant-ish time regardless of user existence
        if user is None or not verify_password(payload.password, user.password_hash) or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        raw, _rt = await _mint_refresh(
            session,
            user_id=user.id,
            tenant_id=user.tenant_id,
            ip=meta["ip"],
            user_agent=meta["user_agent"],
        )
        session.add(AuditEvent(
            tenant_id=user.tenant_id,
            actor_user_id=user.id,
            event_type="auth.login",
            ip=meta["ip"],
            user_agent=meta["user_agent"],
            metadata_json={},
        ))
        access, _claims = issue_access_token(str(user.id), str(user.tenant_id), user.role)
        _set_refresh_cookie(response, raw, _settings.jwt_refresh_ttl)
        return LoginResponse(
            access_token=access,
            expires_in=_settings.jwt_access_ttl,
            user=UserOut.model_validate(user),
        )


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    request: Request,
    response: Response,
    evai_rt: str | None = Cookie(default=None),
) -> LoginResponse:
    if not evai_rt:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    meta = request_meta(request)
    token_hash = hash_refresh_token(evai_rt)

    async with auth_session() as session:
        row = (
            await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        ).scalar_one_or_none()
        if row is None:
            _clear_refresh_cookie(response)
            raise HTTPException(status_code=401, detail="Unknown refresh token")

        now = datetime.now(timezone.utc)
        # Reuse detection: if this refresh token has already been used, invalidate the whole family.
        if row.used or row.revoked or row.expires_at <= now:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == row.family_id)
                .values(revoked=True)
            )
            session.add(AuditEvent(
                tenant_id=row.tenant_id,
                actor_user_id=row.user_id,
                event_type="auth.refresh_reuse_detected",
                ip=meta["ip"],
                user_agent=meta["user_agent"],
                metadata_json={"family_id": str(row.family_id)},
            ))
            _clear_refresh_cookie(response)
            raise HTTPException(status_code=401, detail="Refresh token reuse detected; re-authenticate.")

        # Mint new; mark old used.
        raw_new, new_row = await _mint_refresh(
            session,
            user_id=row.user_id,
            tenant_id=row.tenant_id,
            family_id=row.family_id,
            ip=meta["ip"],
            user_agent=meta["user_agent"],
        )
        row.used = True
        row.replaced_by_id = new_row.id

        # Fetch user for response
        user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()
        access, _claims = issue_access_token(str(user.id), str(user.tenant_id), user.role)
        _set_refresh_cookie(response, raw_new, _settings.jwt_refresh_ttl)
        return LoginResponse(
            access_token=access,
            expires_in=_settings.jwt_access_ttl,
            user=UserOut.model_validate(user),
        )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    evai_rt: str | None = Cookie(default=None),
    current: CurrentUser = Depends(get_current_user),
) -> Response:
    meta = request_meta(request)
    if evai_rt:
        token_hash = hash_refresh_token(evai_rt)
        async with auth_session() as session:
            row = (
                await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
            ).scalar_one_or_none()
            if row is not None:
                # Revoke this specific token (not the whole family; user explicitly logged out).
                row.revoked = True
                session.add(AuditEvent(
                    tenant_id=current.tenant_id,
                    actor_user_id=current.id,
                    event_type="auth.logout",
                    ip=meta["ip"],
                    user_agent=meta["user_agent"],
                    metadata_json={},
                ))
    _clear_refresh_cookie(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=MeResponse)
async def me(current: CurrentUser = Depends(get_current_user)) -> MeResponse:
    # Fetch user in tenant-scoped session, tenant via auth (deny-all RLS on tenants).
    async with app_session(tenant_id=str(current.tenant_id)) as session:
        user = (await session.execute(select(User).where(User.id == current.id))).scalar_one()
    async with auth_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.id == current.tenant_id))).scalar_one()
    return MeResponse(user=UserOut.model_validate(user), tenant=TenantOut.model_validate(tenant))
