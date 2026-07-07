"""FastAPI application entry. Mounts /api router and all sub-routers."""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.init_db import init_db
from app.routers import auth as auth_router
from app.routers import datasets as datasets_router
from app.routers import health as health_router
from app.routers import sessions as sessions_router
from app.routers import agents as agents_router
from app.storage import init_storage_or_log

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    init_storage_or_log()
    logger.info("EvidentAI backend ready.")
    yield


app = FastAPI(title="EvidentAI", version="0.1.0", lifespan=lifespan)

api = APIRouter(prefix="/api")
api.include_router(health_router.router)
api.include_router(auth_router.router)
api.include_router(datasets_router.router)
api.include_router(sessions_router.router)
api.include_router(sessions_router.api_keys_router)
api.include_router(sessions_router.audit_router)
api.include_router(agents_router.analyze_router)
api.include_router(agents_router.jobs_router)


@api.get("/")
async def root() -> dict:
    return {"service": "evidentai", "version": "0.1.0"}


app.include_router(api)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_settings.cors_origins if _settings.cors_origins != ["*"] else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
