"""Health endpoints. No auth required; used by ops + tests."""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from ..db import auth_engine
from ..scanner import scanner_healthy

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> dict:
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}


@router.get("/db")
async def db_health() -> dict:
    try:
        async with auth_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@router.get("/clamav")
async def clamav_health() -> dict:
    ok, info = await scanner_healthy()
    return {"ok": ok, "info": info}
