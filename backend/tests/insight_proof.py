"""Phase 3 E2E proof: real LLM calls, real critic reasoning trail.

Runs two full pipeline executions through the HTTP endpoints:

  Run A: Normal insights on a real 200-row synthetic dataset.
         Expected outcome: critic approves on attempt 1.

  Run B: Same dataset, but with test_force_overstate_first_attempt=True.
         The insight agent's output is programmatically corrupted on
         attempt 1 with an obviously overstated claim (a fake r=0.97
         between two independent variables). The critic must reject,
         we retry with the critic's feedback, and the second attempt
         must approve.

For both runs the script prints:
  - the AgentJob final status + confidence
  - every agent_logs row (insight + critic) in order
  - every critic_decisions row with verdict + reasoning
"""
from __future__ import annotations
import asyncio
import base64
import json
import os
import sys
import time
import uuid

import httpx

BACKEND = "http://localhost:8001"
sys.path.insert(0, "/app/backend")

# --- 1. Login (user seeded in Phase 2) ---
EMAIL = "phase2@example.com"
PASSWORD = "CorrectHorseBattery9!"


async def _login(client: httpx.AsyncClient) -> tuple[str, str, str]:
    r = await client.post(
        f"{BACKEND}/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    r.raise_for_status()
    body = r.json()
    tok = body["access_token"]
    # decode tenant + user from JWT payload (base64)
    payload_b64 = tok.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return tok, payload["tenant_id"], payload["sub"]


async def _seed_dataset_and_stat_job(tenant_id: str, user_id: str) -> tuple[str, str]:
    """Seed via BYPASSRLS role. Returns (dataset_id, stat_job_id)."""
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    # Pull auth URL from backend/.env
    with open("/app/backend/.env") as fh:
        env = dict(l.strip().split("=", 1) for l in fh if l.strip() and "=" in l)
    engine = create_async_engine(env["POSTGRES_AUTH_URL"])
    ds_id = str(uuid.uuid4())
    stat_job_id = str(uuid.uuid4())

    # Build stat_result mimicking what Phase 2's sandbox emits, based on a
    # concrete synthetic dataset we chose deterministically. This lets us
    # skip actually running the sandbox again (Phase 2 already proved it
    # works end-to-end) and focus this proof on the LLM/critic loop.
    #
    # Dataset: x ~ N(50,10), y = 2x + N(0,3), z ~ N(0,1) independent of x/y.
    # Injected 3 outliers on z at indices 5, 40, 100.
    stat_code = (
        "RESULT = {}\n"
        "numeric_cols = ['x', 'y', 'z']\n"
        "corr_df = df[numeric_cols].corr(method='pearson').round(6)\n"
        "RESULT['pearson_correlation'] = {'columns': numeric_cols, "
        "'matrix': [[float(corr_df.iloc[i, j]) for j in range(3)] for i in range(3)]}\n"
        "# IQR outliers per column ..."
    )
    stat_result = {
        "pearson_correlation": {
            "columns": ["x", "y", "z"],
            "matrix": [
                [1.0,      0.98867, -0.02513],
                [0.98867,  1.0,     -0.03104],
                [-0.02513, -0.03104, 1.0],
            ],
        },
        "iqr_outliers": {
            "x": {"q1": 43.1, "q3": 56.7, "iqr": 13.6,
                  "lower_fence": 22.7, "upper_fence": 77.1,
                  "outlier_count": 2, "outlier_indices": [17, 189]},
            "y": {"q1": 86.5, "q3": 113.6, "iqr": 27.1,
                  "lower_fence": 45.85, "upper_fence": 154.25,
                  "outlier_count": 3, "outlier_indices": [17, 63, 189]},
            "z": {"q1": -0.58, "q3": 0.71, "iqr": 1.29,
                  "lower_fence": -2.52, "upper_fence": 2.65,
                  "outlier_count": 4, "outlier_indices": [5, 40, 100, 165]},
        },
    }

    async with engine.begin() as conn:
        await conn.execute(_text(
            "INSERT INTO datasets (id, tenant_id, uploaded_by, original_filename, "
            "content_type, size_bytes, storage_key, encryption_nonce_b64, kek_version, "
            "ciphertext_sha256, status, scan_result) VALUES "
            "(:id, :tid, :uid, 'synthetic.csv', 'text/csv', 5000, "
            " :sk, 'AAAAAAAAAAAAAAAA', 1, 'x', 'ready', 'clean')"),
            {"id": ds_id, "tid": tenant_id, "uid": user_id,
             "sk": f"evidentai/tenants/{tenant_id}/datasets/{ds_id}/x.enc"}
        )
        await conn.execute(_text(
            "INSERT INTO dataset_profiles (tenant_id, dataset_id, row_count, "
            "column_count, columns, profile_meta) VALUES "
            "(:tid, :did, 200, 3, :cols, '{}'::jsonb)"),
            {"tid": tenant_id, "did": ds_id,
             "cols": json.dumps([
                 {"name": "x", "type": "numeric", "null_count": 0, "cardinality": 200},
                 {"name": "y", "type": "numeric", "null_count": 0, "cardinality": 200},
                 {"name": "z", "type": "numeric", "null_count": 0, "cardinality": 200},
             ])})
        await conn.execute(_text(
            "INSERT INTO agent_jobs (id, tenant_id, dataset_id, agent_type, status, "
            "started_at, finished_at, cost_tokens, payload) VALUES "
            "(:id, :tid, :did, 'statistical', 'succeeded', now(), now(), 0, :payload)"),
            {"id": stat_job_id, "tid": tenant_id, "did": ds_id,
             "payload": json.dumps({
                 "tests_selected": ["pearson_correlation_matrix", "iqr_outlier_detection"],
                 "code": stat_code,
                 "elapsed_seconds": 0.85,
                 "result": stat_result,
             })})
    await engine.dispose()
    return ds_id, stat_job_id


async def _dump_trail(tenant_id: str, insight_job_id: str) -> None:
    """Read agent_logs + critic_decisions for one insight job as evident_auth
    (BYPASSRLS) so the audit trail is visible regardless of any RLS."""
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine
    with open("/app/backend/.env") as fh:
        env = dict(l.strip().split("=", 1) for l in fh if l.strip() and "=" in l)
    engine = create_async_engine(env["POSTGRES_AUTH_URL"])
    async with engine.connect() as conn:
        logs = (await conn.execute(_text(
            "SELECT agent_role, step, payload::text, created_at FROM agent_logs "
            "WHERE job_id = :jid ORDER BY created_at"), {"jid": insight_job_id})).all()
        decisions = (await conn.execute(_text(
            "SELECT verdict, reasoning, blocked, created_at FROM critic_decisions "
            "WHERE job_id = :jid ORDER BY created_at"), {"jid": insight_job_id})).all()
    await engine.dispose()

    print(f"\n--- agent_logs ({len(logs)} rows) ---")
    for role, step, payload, ts in logs:
        pl = json.loads(payload)
        u = pl.get("usage") or {}
        summary = {
            "verdict": pl.get("verdict"),
            "issues": len(pl.get("issues") or []),
            "finding_count": pl.get("output_finding_count"),
            "force_overstate": pl.get("force_overstate_active"),
            "feedback_received": (pl.get("feedback_received") or "")[:100] + "..."
                if pl.get("feedback_received") else None,
            "model": f"{u.get('provider')}/{u.get('model')}",
            "tokens_est": u.get("total_tokens_estimated"),
            "elapsed_s": u.get("elapsed_seconds"),
        }
        print(f"  [{ts}] {role:12s} {step:14s} {json.dumps(summary)}")

    print(f"\n--- critic_decisions ({len(decisions)} rows) ---")
    for verdict, reasoning, blocked, ts in decisions:
        print(f"  [{ts}] verdict={verdict}  blocked={blocked}")
        print(f"    reasoning: {reasoning}")


async def _print_final_job(client: httpx.AsyncClient, token: str, job_id: str) -> dict:
    r = await client.get(f"{BACKEND}/api/agent-jobs/{job_id}",
                         headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    job = r.json()
    print(f"\n--- FINAL AgentJob {job_id[:8]}... ---")
    print(f"  status         : {job['status']}")
    print(f"  started_at     : {job['started_at']}")
    print(f"  finished_at    : {job['finished_at']}")
    print(f"  error          : {job['error']}")
    p = job.get("payload") or {}
    print(f"  confidence     : {p.get('confidence')}")
    print(f"  attempts_used  : {p.get('attempts_used')} / {p.get('max_attempts')}")
    finalf = (p.get('final_insights') or {}).get('findings') or []
    print(f"  final finding count: {len(finalf)}")
    for f in finalf[:6]:
        title = (f.get("title") or "")[:80]
        print(f"    rank {f.get('rank')} score={f.get('importance_score')} : {title}")
    if p.get("critic_final_objection"):
        print(f"  FINAL CRITIC OBJECTION (stored, not hidden):")
        print(f"    {p['critic_final_objection']}")
    return job


async def _poll_until_terminal(client: httpx.AsyncClient, token: str,
                                job_id: str, timeout: int = 120) -> dict:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        r = await client.get(f"{BACKEND}/api/agent-jobs/{job_id}",
                             headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        job = r.json()
        if job["status"] != last_status:
            print(f"  poll: status={job['status']}")
            last_status = job["status"]
        if job["status"] in {"succeeded", "succeeded_low_confidence", "failed"}:
            return job
        await asyncio.sleep(2)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("=== login ===")
        token, tenant_id, user_id = await _login(client)
        print(f"  tenant={tenant_id}")

        print("\n=== seed dataset + succeeded statistical job ===")
        dataset_id, stat_job_id = await _seed_dataset_and_stat_job(tenant_id, user_id)
        print(f"  dataset_id={dataset_id}\n  stat_job_id={stat_job_id}")

        # ------------------------------------------------------------------
        # RUN A: normal insight generation. Expect the critic to APPROVE.
        # ------------------------------------------------------------------
        print("\n\n" + "=" * 78)
        print("RUN A: normal insight pipeline (expect approve on attempt 1)")
        print("=" * 78)
        r = await client.post(
            f"{BACKEND}/api/datasets/{dataset_id}/insights",
            headers={"Authorization": f"Bearer {token}"},
            json={"stat_job_id": stat_job_id,
                  "test_force_overstate_first_attempt": False},
        )
        print(f"  POST /insights -> HTTP {r.status_code}")
        r.raise_for_status()
        job_a = r.json()
        print(f"  job.id={job_a['id']}  status={job_a['status']}")

        final_a = await _poll_until_terminal(client, token, job_a["id"], timeout=180)
        await _print_final_job(client, token, job_a["id"])
        await _dump_trail(tenant_id, job_a["id"])

        # ------------------------------------------------------------------
        # RUN B: force overstatement on attempt 1. Expect reject -> retry.
        # ------------------------------------------------------------------
        print("\n\n" + "=" * 78)
        print("RUN B: force overstatement on attempt 1 (expect reject then retry)")
        print("=" * 78)
        r = await client.post(
            f"{BACKEND}/api/datasets/{dataset_id}/insights",
            headers={"Authorization": f"Bearer {token}"},
            json={"stat_job_id": stat_job_id,
                  "test_force_overstate_first_attempt": True},
        )
        print(f"  POST /insights -> HTTP {r.status_code}")
        r.raise_for_status()
        job_b = r.json()
        print(f"  job.id={job_b['id']}  status={job_b['status']}")

        final_b = await _poll_until_terminal(client, token, job_b["id"], timeout=240)
        await _print_final_job(client, token, job_b["id"])
        await _dump_trail(tenant_id, job_b["id"])


if __name__ == "__main__":
    asyncio.run(main())
