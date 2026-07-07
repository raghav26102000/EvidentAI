"""Phase 2 statistical analysis endpoints.

    POST /api/datasets/{dataset_id}/analyze
        Creates an AgentJob(agent_type="statistical", status="pending"),
        schedules background execution, returns 202 with job id.

    GET /api/agent-jobs/{job_id}
        Returns current status + result (payload) of an AgentJob.
        Tenant-scoped via RLS.

Execution flow (background):
    pending -> running (started_at set)
      -> fetch ciphertext from object storage
      -> decrypt with tenant DEK (Phase 1 KMS)
      -> load DatasetProfile.columns (Phase 1 profiler output)
      -> statistical_agent.run_statistical_analysis(...)  (Phase 2 sandbox)
      -> succeeded (payload = {tests_selected, code, result,
                               elapsed_seconds, sandbox_stdout_logs})
    On any exception: failed (error set, finished_at set).
"""
from __future__ import annotations
import asyncio
import base64
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.statistical_agent import run_statistical_analysis
from ..db import app_session
from ..deps import CurrentUser, get_current_user, tenant_session
from ..kms import EncryptedBlob, decrypt_with_dek
from ..models import AgentJob, AuditEvent, Dataset, DatasetProfile
from ..sandbox import SandboxLimits
from ..storage import StorageError, get_object

# Same helper used by datasets.py — re-imported to avoid circular imports.
from .datasets import _get_tenant_dek

logger = logging.getLogger(__name__)

analyze_router = APIRouter(prefix="/datasets", tags=["agents"])
jobs_router = APIRouter(prefix="/agent-jobs", tags=["agents"])


class JobCreatedOut(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    agent_type: str
    status: str
    created_at: datetime


class AgentJobOut(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID | None
    agent_type: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    payload: dict = Field(default_factory=dict)
    created_at: datetime


# ---------------------------------------------------------------------------
# POST /api/datasets/{dataset_id}/analyze
# ---------------------------------------------------------------------------
@analyze_router.post(
    "/{dataset_id}/analyze",
    response_model=JobCreatedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def analyze_dataset(
    dataset_id: uuid.UUID,
    session: AsyncSession = Depends(tenant_session),
    current: CurrentUser = Depends(get_current_user),
) -> JobCreatedOut:
    # Verify dataset exists in tenant, is ready, and has a profile.
    ds = (
        await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    ).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if ds.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Dataset not ready for analysis (status={ds.status})",
        )
    prof = (
        await session.execute(
            select(DatasetProfile).where(DatasetProfile.dataset_id == dataset_id)
        )
    ).scalar_one_or_none()
    if prof is None:
        raise HTTPException(status_code=409, detail="Profile not available")

    # Insert job (RLS enforces tenant scoping via the app session).
    job = AgentJob(
        tenant_id=current.tenant_id,
        dataset_id=dataset_id,
        agent_type="statistical",
        status="pending",
        payload={},
    )
    session.add(job)
    session.add(AuditEvent(
        tenant_id=current.tenant_id,
        actor_user_id=current.id,
        event_type="agent_job.created",
        resource_type="agent_job",
        resource_id=str(job.id),
        metadata_json={"dataset_id": str(dataset_id), "agent_type": "statistical"},
    ))
    await session.flush()
    job_id = job.id

    # Fire-and-forget background execution. The task manages its own DB session.
    asyncio.create_task(
        _run_statistical_job(
            job_id=job_id,
            tenant_id=current.tenant_id,
            dataset_id=dataset_id,
        )
    )

    return JobCreatedOut(
        id=job_id,
        dataset_id=dataset_id,
        agent_type=job.agent_type,
        status=job.status,
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# GET /api/agent-jobs/{job_id}
# ---------------------------------------------------------------------------
@jobs_router.get("/{job_id}", response_model=AgentJobOut)
async def get_agent_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(tenant_session),
    _current: CurrentUser = Depends(get_current_user),
) -> AgentJobOut:
    job = (
        await session.execute(select(AgentJob).where(AgentJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Agent job not found")
    return AgentJobOut(
        id=job.id,
        dataset_id=job.dataset_id,
        agent_type=job.agent_type,
        status=job.status,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        payload=job.payload or {},
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# Background worker (runs outside the request scope)
# ---------------------------------------------------------------------------
async def _set_job_state(
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    **fields,
) -> None:
    async with app_session(tenant_id=str(tenant_id)) as session:
        job = (
            await session.execute(select(AgentJob).where(AgentJob.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            logger.warning("agent job %s vanished before state update", job_id)
            return
        for k, v in fields.items():
            setattr(job, k, v)


async def _run_statistical_job(
    *,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
    dataset_id: uuid.UUID,
) -> None:
    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    try:
        await _set_job_state(tenant_id, job_id, status="running", started_at=now())

        # 1. Load dataset row + profile in the tenant's scope.
        async with app_session(tenant_id=str(tenant_id)) as session:
            ds = (
                await session.execute(select(Dataset).where(Dataset.id == dataset_id))
            ).scalar_one_or_none()
            prof = (
                await session.execute(
                    select(DatasetProfile).where(DatasetProfile.dataset_id == dataset_id)
                )
            ).scalar_one_or_none()
        if ds is None or prof is None:
            raise RuntimeError("dataset_or_profile_missing")
        if not ds.storage_key or not ds.encryption_nonce_b64:
            raise RuntimeError("dataset_ciphertext_metadata_missing")

        # 2. Fetch ciphertext + decrypt with the tenant DEK.
        try:
            ciphertext = await asyncio.to_thread(get_object, ds.storage_key)
        except StorageError as e:
            raise RuntimeError(f"storage_unavailable: {e}") from e
        dek = await _get_tenant_dek(tenant_id)
        blob = EncryptedBlob(
            nonce=base64.b64decode(ds.encryption_nonce_b64),
            ciphertext=ciphertext,
        )
        aad = f"tenant={tenant_id};dataset={dataset_id}".encode()
        plaintext = decrypt_with_dek(dek, blob, aad=aad)

        # 3. Only CSV is supported by the stats worker in this phase.
        ext = (ds.original_filename or "").lower().rsplit(".", 1)[-1]
        if ext != "csv":
            raise RuntimeError(f"unsupported_format:{ext} (phase 2 supports csv only)")

        # 4. Run the sandboxed statistical agent.
        limits = SandboxLimits(wall_timeout_seconds=20)
        result = await run_statistical_analysis(
            profile_columns=prof.columns,
            dataset_csv_bytes=plaintext,
            limits=limits,
        )
        sandbox_result = result.get("sandbox_result") or {}
        sandbox_ok = bool(result.get("sandbox_ok")) and bool(sandbox_result.get("ok"))

        if not sandbox_ok:
            await _set_job_state(
                tenant_id, job_id,
                status="failed",
                finished_at=now(),
                error=(
                    f"sandbox_exit={result.get('exit_code')} "
                    f"signal={result.get('killed_by_signal')} "
                    f"timed_out={result.get('timed_out')} "
                    f"detail={str(sandbox_result)[:400]}"
                ),
                payload={
                    "tests_selected": result.get("tests_selected", []),
                    "code": result.get("code"),
                    "elapsed_seconds": result.get("elapsed_seconds"),
                    "sandbox_stdout_logs": result.get("sandbox_stdout_logs", "")[:4000],
                    "sandbox_stderr_logs": result.get("sandbox_stderr_logs", "")[:4000],
                },
            )
            return

        # 5. Success: persist result.
        await _set_job_state(
            tenant_id, job_id,
            status="succeeded",
            finished_at=now(),
            payload={
                "tests_selected": result.get("tests_selected", []),
                "code": result.get("code"),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "sandbox_stdout_logs": result.get("sandbox_stdout_logs", "")[:4000],
                "result": sandbox_result.get("result"),
                "seccomp_denied_syscalls": sandbox_result.get("seccomp_denied_syscalls", []),
            },
        )

    except Exception as e:  # noqa: BLE001
        logger.exception("agent job %s failed", job_id)
        await _set_job_state(
            tenant_id, job_id,
            status="failed",
            finished_at=now(),
            error=f"{type(e).__name__}: {e}"[:2000],
        )
