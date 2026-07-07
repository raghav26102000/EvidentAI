"""FastAPI dependencies: current user, tenant-scoped DB session, audit helper."""
from __future__ import annotations
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import app_session, auth_session
from .models import User
from .security import verify_access_token


@dataclass
class CurrentUser:
    id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    email: str


async def get_current_user(authorization: Optional[str] = Header(None)) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    claims = verify_access_token(token)
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    # Verify the user still exists / is active — this uses the app session
    # scoped to the tenant (RLS enforces isolation).
    async with app_session(tenant_id=claims.tenant_id) as session:
        row = (
            await session.execute(select(User).where(User.id == uuid.UUID(claims.sub)))
        ).scalar_one_or_none()
        if row is None or not row.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
        return CurrentUser(
            id=row.id, tenant_id=row.tenant_id, role=row.role, email=row.email
        )


async def tenant_session(user: CurrentUser = Depends(get_current_user)) -> AsyncIterator[AsyncSession]:
    """Yield a DB session bound to the current user's tenant (RLS applied)."""
    async with app_session(tenant_id=str(user.tenant_id)) as session:
        yield session


def require_role(*roles: str):
    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user
    return _dep


def request_meta(request: Request) -> dict:
    return {
        "ip": (request.client.host if request.client else None),
        "user_agent": (request.headers.get("user-agent") or "")[:400],
    }
