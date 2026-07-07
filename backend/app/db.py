"""Async SQLAlchemy engines + session factories with RLS enforcement.

Two engines:
- ``app_engine``: connects as ``evident`` (NOT bypassrls). All tenant-scoped
  queries use this. RLS is enforced by Postgres regardless of ORM code.
- ``auth_engine``: connects as ``evident_auth`` (BYPASSRLS). Only used for
  the small set of cross-tenant auth lookups (email lookup at login,
  refresh token lookup, tenant creation at signup).
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings

_settings = get_settings()

app_engine: AsyncEngine = create_async_engine(
    _settings.postgres_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)
auth_engine: AsyncEngine = create_async_engine(
    _settings.postgres_auth_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,
)

AppSessionLocal = async_sessionmaker(app_engine, expire_on_commit=False, class_=AsyncSession)
AuthSessionLocal = async_sessionmaker(auth_engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def app_session(tenant_id: Optional[str] = None) -> AsyncIterator[AsyncSession]:
    """Yields a session that enforces RLS for the given tenant.

    A single transaction is opened; ``SET LOCAL app.current_tenant`` is issued
    so that Postgres RLS policies evaluate against this UUID. If ``tenant_id``
    is None the session runs with NO tenant context, meaning every RLS-scoped
    SELECT will return zero rows (fail-closed). This is intentional.
    """
    async with AppSessionLocal() as session:
        async with session.begin():
            if tenant_id is not None:
                # set_config is parameterized (safer than string interpolation)
                await session.execute(
                    text("SELECT set_config('app.current_tenant', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
            yield session


@asynccontextmanager
async def auth_session() -> AsyncIterator[AsyncSession]:
    """Cross-tenant lookups (login, refresh, tenant creation). BYPASSRLS role."""
    async with AuthSessionLocal() as session:
        async with session.begin():
            yield session
