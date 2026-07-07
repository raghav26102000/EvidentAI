"""Dataset upload, list, get, profile endpoints.

Upload pipeline (strict order):
    1) size cap enforced during stream read
    2) extension allowlist + Excel/CSV structural hardening
    3) ClamAV scan (fail-closed on any error)
    4) AES-256-GCM encrypt with per-tenant DEK, fresh nonce
    5) push ciphertext to object storage
    6) persist dataset row (status = scan_clean)
    7) run deterministic profiler in sandbox (status = profiling -> ready)
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import auth_session
from ..deps import CurrentUser, get_current_user, request_meta, tenant_session
from ..kms import EncryptedBlob, WrappedDEK, decrypt_with_dek, encrypt_with_dek, unwrap_dek
from ..models import AuditEvent, Dataset, DatasetProfile, Tenant
from ..sandbox import SandboxError, SandboxLimits, SandboxTimeout, WORKER_DIR, run_worker
from ..scanner import (
    ScanUnavailable,
    VirusDetected,
    detect_and_harden,
    scan_bytes,
    ALLOWED_EXT_TO_MIME,
)
from ..schemas import DatasetOut, DatasetProfileOut, ColumnProfileOut
from ..storage import build_object_key, get_object, put_object

router = APIRouter(prefix="/datasets", tags=["datasets"])
_settings = get_settings()


async def _get_tenant_dek(tenant_id: uuid.UUID) -> bytes:
    async with auth_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        wrapped = WrappedDEK.from_json(tenant.wrapped_dek)
    return unwrap_dek(wrapped)


@router.get("", response_model=list[DatasetOut])
async def list_datasets(
    session: AsyncSession = Depends(tenant_session),
    _current: CurrentUser = Depends(get_current_user),
) -> list[DatasetOut]:
    rows = (
        await session.execute(select(Dataset).order_by(Dataset.created_at.desc()).limit(200))
    ).scalars().all()
    return [DatasetOut.model_validate(r) for r in rows]


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get_dataset(
    dataset_id: uuid.UUID,
    session: AsyncSession = Depends(tenant_session),
    _current: CurrentUser = Depends(get_current_user),
) -> DatasetOut:
    ds = (await session.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return DatasetOut.model_validate(ds)


@router.get("/{dataset_id}/profile", response_model=DatasetProfileOut)
async def get_profile(
    dataset_id: uuid.UUID,
    session: AsyncSession = Depends(tenant_session),
    _current: CurrentUser = Depends(get_current_user),
) -> DatasetProfileOut:
    prof = (
        await session.execute(select(DatasetProfile).where(DatasetProfile.dataset_id == dataset_id))
    ).scalar_one_or_none()
    if prof is None:
        raise HTTPException(status_code=404, detail="Profile not ready")
    return DatasetProfileOut(
        id=prof.id,
        dataset_id=prof.dataset_id,
        row_count=prof.row_count,
        column_count=prof.column_count,
        columns=[ColumnProfileOut(**c) for c in prof.columns],
        profile_meta=prof.profile_meta,
        created_at=prof.created_at,
    )


@router.post("", response_model=DatasetOut, status_code=201)
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
) -> DatasetOut:
    meta = request_meta(request)
    filename = file.filename or "upload.bin"
    lower = filename.lower()

    ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
    if ext not in ALLOWED_EXT_TO_MIME:
        raise HTTPException(status_code=415, detail=f"Only CSV and Excel (.xlsx) are accepted (got .{ext}).")

    # ---- Stream to memory with strict size cap (Phase 1: 50MB default).
    limit = _settings.max_upload_bytes
    buf = bytearray()
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(status_code=413, detail=f"File exceeds {limit} bytes limit")
    data = bytes(buf)
    size_bytes = len(data)
    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # ---- Structural hardening BEFORE scan (cheap + fail-fast on obvious badness).
    harden = detect_and_harden(filename, data)
    if not harden.ok:
        raise HTTPException(status_code=422, detail=f"File rejected: {harden.reason}")

    # ---- Malware scan.
    try:
        await scan_bytes(data)
        scan_result = "clean"
    except VirusDetected as e:
        # Persist a failed row so audit trail exists.
        await _persist_failed(
            current=current,
            filename=filename,
            content_type=ALLOWED_EXT_TO_MIME[ext],
            size_bytes=size_bytes,
            reason=f"virus_detected:{e.signature}",
            meta=meta,
        )
        raise HTTPException(status_code=422, detail=f"Malware detected: {e.signature}")
    except ScanUnavailable as e:
        raise HTTPException(status_code=503, detail=f"scan_unavailable: {e}")

    # ---- Encrypt.
    dek = await _get_tenant_dek(current.tenant_id)
    dataset_id = uuid.uuid4()
    aad = f"tenant={current.tenant_id};dataset={dataset_id}".encode()
    blob = encrypt_with_dek(dek, data, aad=aad)
    ciphertext_sha = hashlib.sha256(blob.ciphertext).hexdigest()

    # ---- Upload to object storage.
    key = build_object_key(str(current.tenant_id), str(dataset_id))
    try:
        put_object(key, blob.ciphertext)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"storage_upload_failed: {e}")

    # ---- Persist dataset row.
    from ..db import app_session
    async with app_session(tenant_id=str(current.tenant_id)) as session:
        ds = Dataset(
            id=dataset_id,
            tenant_id=current.tenant_id,
            uploaded_by=current.id,
            original_filename=filename,
            content_type=ALLOWED_EXT_TO_MIME[ext],
            size_bytes=size_bytes,
            storage_key=key,
            encryption_nonce_b64=base64.b64encode(blob.nonce).decode(),
            kek_version=1,
            ciphertext_sha256=ciphertext_sha,
            status="profiling",
            scan_result=scan_result,
        )
        session.add(ds)
        session.add(AuditEvent(
            tenant_id=current.tenant_id,
            actor_user_id=current.id,
            event_type="dataset.uploaded",
            resource_type="dataset",
            resource_id=str(dataset_id),
            ip=meta["ip"],
            user_agent=meta["user_agent"],
            metadata_json={"size": size_bytes, "filename": filename},
        ))

    # ---- Run deterministic profiler in sandbox.
    try:
        profile = await _profile_dataset(data=data, ext=ext)
        async with app_session(tenant_id=str(current.tenant_id)) as session:
            ds = (
                await session.execute(select(Dataset).where(Dataset.id == dataset_id))
            ).scalar_one()
            prof = DatasetProfile(
                tenant_id=current.tenant_id,
                dataset_id=dataset_id,
                row_count=profile["row_count"],
                column_count=profile["column_count"],
                columns=profile["columns"],
                profile_meta=profile.get("profile_meta", {}),
            )
            session.add(prof)
            ds.status = "ready"
            ds.updated_at = datetime.now(timezone.utc)
            session.add(AuditEvent(
                tenant_id=current.tenant_id,
                actor_user_id=current.id,
                event_type="dataset.profiled",
                resource_type="dataset",
                resource_id=str(dataset_id),
                ip=meta["ip"],
                user_agent=meta["user_agent"],
                metadata_json={
                    "rows": profile["row_count"],
                    "cols": profile["column_count"],
                },
            ))
    except (SandboxTimeout, SandboxError) as e:
        async with app_session(tenant_id=str(current.tenant_id)) as session:
            ds = (await session.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one()
            ds.status = "failed"
            ds.error_message = f"profiler:{e}"
            ds.updated_at = datetime.now(timezone.utc)

    async with app_session(tenant_id=str(current.tenant_id)) as session:
        ds = (await session.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one()
        return DatasetOut.model_validate(ds)


async def _persist_failed(
    *, current: CurrentUser, filename: str, content_type: str, size_bytes: int, reason: str, meta: dict
) -> None:
    from ..db import app_session
    async with app_session(tenant_id=str(current.tenant_id)) as session:
        session.add(Dataset(
            tenant_id=current.tenant_id,
            uploaded_by=current.id,
            original_filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            status="failed",
            scan_result=reason,
            error_message=reason,
        ))
        session.add(AuditEvent(
            tenant_id=current.tenant_id,
            actor_user_id=current.id,
            event_type="dataset.rejected",
            resource_type="dataset",
            ip=meta["ip"],
            user_agent=meta["user_agent"],
            metadata_json={"reason": reason, "filename": filename},
        ))


async def _profile_dataset(*, data: bytes, ext: str) -> dict:
    # Materialize the plaintext to a tmp file that only the sandbox will read;
    # sandbox has no network, tight CPU/memory/wall limits.
    with tempfile.NamedTemporaryFile(prefix="evai-prof-", suffix=f".{ext}", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        os.chmod(tmp_path, 0o600)
        worker = str(WORKER_DIR / "profiler_worker.py")
        ctl = json.dumps({"format": ext, "path": tmp_path}).encode()
        return await run_worker(worker, ctl, SandboxLimits())
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
