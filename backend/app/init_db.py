"""Idempotent schema bootstrap: creates tables and enforces RLS policies.

Called at FastAPI startup. Safe to call repeatedly.
"""
from __future__ import annotations
import asyncio
import logging

from sqlalchemy import text

from .db import auth_engine
from .models import Base, TENANT_SCOPED_TABLES

logger = logging.getLogger(__name__)


async def init_db() -> None:
    # 1) Create all tables (as the auth/superuser-ish role that owns them).
    async with auth_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2) Grant table access to the app role.
    async with auth_engine.begin() as conn:
        await conn.execute(text(
            "GRANT USAGE ON SCHEMA public TO evident;"
        ))
        await conn.execute(text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO evident;"
        ))
        await conn.execute(text(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO evident;"
        ))
        await conn.execute(text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO evident;"
        ))

    # 3) Enable + FORCE RLS on tenant-scoped tables and install policies.
    async with auth_engine.begin() as conn:
        for table in TENANT_SCOPED_TABLES:
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"))
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"))
            # Drop existing policy if present (idempotent).
            await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table};"))
            await conn.execute(text(
                f"CREATE POLICY tenant_isolation ON {table} "
                f"USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
                f"WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
            ))

        # ``tenants`` table: only accessible via BYPASSRLS auth session.
        # We still enable RLS with a no-op policy that denies everyone by
        # default, so the app role literally cannot read the tenants table.
        await conn.execute(text("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;"))
        await conn.execute(text("ALTER TABLE tenants FORCE ROW LEVEL SECURITY;"))
        await conn.execute(text("DROP POLICY IF EXISTS tenants_deny_all ON tenants;"))
        await conn.execute(text(
            "CREATE POLICY tenants_deny_all ON tenants USING (false) WITH CHECK (false);"
        ))

    logger.info("DB init complete: tables created, RLS enforced on %d tables.", len(TENANT_SCOPED_TABLES))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_db())
