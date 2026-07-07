"""Idempotent demo-data seeder.

Populates one dataset per tenant showing the full Run B story:
  * an "insight" AgentJob that shows a critic rejection followed by an
    approved retry, exactly matching what the live Phase-3 pipeline produced,
  * the underlying "statistical" AgentJob it depends on,
  * the AgentLog + CriticDecision rows the agent-trail view reads,
  * a DatasetProfile so the analysis dashboard's charts render.

Fully deterministic — no LLM calls. The numbers are the exact ones the
real pipeline emitted the first time it ran (captured from the earlier
E2E proof run), so:
  - the heatmap, outlier plot, and written findings all reference the
    SAME underlying data,
  - the reject/retry story is real prose from the actual critic call,
    not a placeholder.

Idempotent: keyed off ``datasets.original_filename == DEMO_FILENAME`` per
tenant. Safe to call on every startup.
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text as _sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import auth_session
from .models import (
    AgentJob, AgentLog, CriticDecision, Dataset, DatasetProfile, Tenant,
)

logger = logging.getLogger(__name__)

DEMO_FILENAME = "example-correlation-review.csv"
DEMO_LABEL = "Example · correlation review with agent critique"

# ------- The Run B payload as it actually came out of the live pipeline ------
_STAT_CODE = (
    "RESULT = {}\n"
    "numeric_cols = ['x', 'y', 'z']\n"
    "if len(numeric_cols) >= 2:\n"
    "    corr_df = df[numeric_cols].corr(method='pearson').round(6)\n"
    "    RESULT['pearson_correlation'] = {\n"
    "        'columns': numeric_cols,\n"
    "        'matrix': [[float(corr_df.iloc[i, j]) for j in range(3)] for i in range(3)],\n"
    "    }\n"
    "outliers = {}\n"
    "for col in numeric_cols:\n"
    "    s = df[col].dropna()\n"
    "    q1 = float(s.quantile(0.25)); q3 = float(s.quantile(0.75))\n"
    "    iqr = q3 - q1; low = q1 - 1.5*iqr; high = q3 + 1.5*iqr\n"
    "    mask = (s < low) | (s > high)\n"
    "    outliers[col] = {'q1': q1, 'q3': q3, 'iqr': iqr,\n"
    "                     'lower_fence': float(low), 'upper_fence': float(high),\n"
    "                     'outlier_count': int(mask.sum()),\n"
    "                     'outlier_indices': [int(i) for i in s.index[mask].tolist()[:50]]}\n"
    "RESULT['iqr_outliers'] = outliers\n"
)

_STAT_RESULT: dict[str, Any] = {
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

_PROFILE_COLUMNS = [
    {"name": "x", "type": "numeric", "null_count": 0, "cardinality": 200},
    {"name": "y", "type": "numeric", "null_count": 0, "cardinality": 200},
    {"name": "z", "type": "numeric", "null_count": 0, "cardinality": 200},
]

# --- The attempt-1 (rejected, overstated) insight output --------------------
_A1_INSIGHT_FINDINGS = {
    "findings": [
        {
            "rank": 1,
            "title": "TESTING OVERSTATEMENT: z is near-perfectly correlated with x",
            "claim": ("The variable z shows a near-perfect positive correlation "
                      "with x (r > 0.95), indicating a very strong linear dependence."),
            "importance_score": 0.99,
            "backed_by": ["pearson_correlation"],
            "evidence": {"r_xz_claimed": 0.97},
        },
        {
            "rank": 2,
            "title": "x and y move together almost perfectly",
            "claim": "x and y have a strong positive Pearson correlation of 0.98867.",
            "importance_score": 0.94,
            "backed_by": ["pearson_correlation"],
            "evidence": {"r": 0.98867},
        },
        {
            "rank": 3,
            "title": "z has isolated outliers",
            "claim": ("z has 4 outliers out of 200 rows (2%); these appear isolated, "
                      "not a broad pattern."),
            "importance_score": 0.42,
            "backed_by": ["iqr_outliers"],
            "evidence": {"count": 4, "n": 200},
        },
    ],
    "summary": "x and y are strongly related; z is essentially independent with a small number of isolated outliers.",
}

_A1_CRITIC_REASONING = (
    "The statistical methods are appropriate (Pearson on numeric columns with n=200, "
    "IQR outlier detection). However, Finding #1 (rank 1) contains a critical factual "
    "error: it claims z is near-perfectly correlated with x (r > 0.95, "
    "r_xz_claimed=0.97), when the actual stat result shows r_xz = -0.02513, which is "
    "essentially zero. This is a direct contradiction of the numeric evidence and a "
    "severe misrepresentation."
)

_A1_CRITIC_ISSUES = [
    {
        "target": "insight_agent",
        "finding_rank_or_test": 1,
        "problem": ("Finding #1 claims r_xz > 0.95 but the stat result matrix "
                    "shows r_xz = -0.02513. This is a factual contradiction."),
        "suggestion": ("Remove Finding #1 entirely. If a finding about z's relationship "
                       "with x is included, describe it as 'essentially absent'."),
    },
]

# --- The attempt-2 (approved) insight output --------------------------------
_A2_INSIGHT_FINDINGS = {
    "findings": [
        {
            "rank": 1,
            "title": "x and y move together almost perfectly",
            "claim": ("x and y have a strong positive relationship, with a Pearson "
                      "correlation of 0.98867."),
            "importance_score": 0.98,
            "backed_by": ["pearson_correlation"],
            "evidence": {"r": 0.98867},
        },
        {
            "rank": 2,
            "title": "z shows isolated outliers more often than x or y",
            "claim": "z has 4 outliers, x has 2, y has 3. All rates are below 2% of n.",
            "importance_score": 0.42,
            "backed_by": ["iqr_outliers"],
            "evidence": {"z": 4, "x": 2, "y": 3, "n": 200},
        },
        {
            "rank": 3,
            "title": "y has isolated outliers",
            "claim": "y has 3 out of 200 rows outside the IQR fences (1.5%).",
            "importance_score": 0.34,
            "backed_by": ["iqr_outliers"],
            "evidence": {"count": 3, "n": 200},
        },
        {
            "rank": 4,
            "title": "x has isolated outliers",
            "claim": "x has 2 out of 200 rows outside the IQR fences (1%).",
            "importance_score": 0.27,
            "backed_by": ["iqr_outliers"],
            "evidence": {"count": 2, "n": 200},
        },
        {
            "rank": 5,
            "title": "x and z have essentially no relationship",
            "claim": "Pearson r between x and z is -0.02513 — indistinguishable from zero.",
            "importance_score": 0.12,
            "backed_by": ["pearson_correlation"],
            "evidence": {"r": -0.02513},
        },
        {
            "rank": 6,
            "title": "y and z have essentially no relationship",
            "claim": "Pearson r between y and z is -0.03104 — indistinguishable from zero.",
            "importance_score": 0.11,
            "backed_by": ["pearson_correlation"],
            "evidence": {"r": -0.03104},
        },
    ],
    "summary": ("x and y are strongly related; z is essentially independent of both. "
                "Outlier counts are low across all columns and best described as isolated."),
}

_A2_CRITIC_REASONING = (
    "Statistical methods are appropriate: Pearson correlation is applied to three "
    "numeric columns with n=200 and sufficient cardinality, and IQR outlier detection "
    "is standard. All insight claims are faithfully grounded in the stat results: "
    "r=0.98867 correctly labelled 'strong', near-zero correlations (-0.025, -0.031) "
    "correctly labelled 'essentially absent', and outlier counts/rates are "
    "arithmetically correct and appropriately described as 'isolated' rather than a "
    "broad pattern."
)


async def _seed_for_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Returns True if the demo was newly created, False if already present."""
    existing = (
        await session.execute(
            _sql_text(
                "SELECT id FROM datasets "
                "WHERE tenant_id = :tid AND original_filename = :fn LIMIT 1"
            ),
            {"tid": str(tenant_id), "fn": DEMO_FILENAME},
        )
    ).first()
    if existing:
        return False

    now = datetime.now(timezone.utc)
    ds_id = uuid.uuid4()
    stat_job_id = uuid.uuid4()
    insight_job_id = uuid.uuid4()

    # 1. Dataset row (no storage backing; is_demo flag stored in scan_result field —
    #    but scan_result is a bounded enum, so we use metadata via storage_key naming).
    await session.execute(
        _sql_text(
            "INSERT INTO datasets (id, tenant_id, uploaded_by, original_filename, "
            "content_type, size_bytes, storage_key, encryption_nonce_b64, kek_version, "
            "ciphertext_sha256, status, scan_result, created_at, updated_at) VALUES "
            "(:id, :tid, NULL, :fn, 'text/csv', 5000, :sk, 'AAAAAAAAAAAAAAAA', 1, "
            "'demo-seed', 'ready', 'clean', :now, :now)"
        ),
        {
            "id": str(ds_id), "tid": str(tenant_id), "fn": DEMO_FILENAME,
            "sk": f"demo-seed/tenants/{tenant_id}/datasets/{ds_id}/example.enc",
            "now": now,
        },
    )
    # 2. Profile
    await session.execute(
        _sql_text(
            "INSERT INTO dataset_profiles (tenant_id, dataset_id, row_count, "
            "column_count, columns, profile_meta, created_at) VALUES "
            "(:tid, :did, 200, 3, :cols, :meta, :now)"
        ),
        {
            "tid": str(tenant_id), "did": str(ds_id),
            "cols": json.dumps(_PROFILE_COLUMNS),
            "meta": json.dumps({"seeded_demo": True, "label": DEMO_LABEL}),
            "now": now,
        },
    )
    # 3. Statistical AgentJob (succeeded)
    await session.execute(
        _sql_text(
            "INSERT INTO agent_jobs (id, tenant_id, dataset_id, agent_type, status, "
            "started_at, finished_at, cost_tokens, payload, created_at) VALUES "
            "(:id, :tid, :did, 'statistical', 'succeeded', :s, :f, 0, :payload, :now)"
        ),
        {
            "id": str(stat_job_id), "tid": str(tenant_id), "did": str(ds_id),
            "s": now - timedelta(seconds=6), "f": now - timedelta(seconds=5),
            "payload": json.dumps({
                "tests_selected": ["pearson_correlation_matrix", "iqr_outlier_detection"],
                "code": _STAT_CODE,
                "elapsed_seconds": 0.85,
                "sandbox_stdout_logs": "",
                "result": _STAT_RESULT,
                "seccomp_denied_syscalls": [
                    "socket", "openat", "execve", "clone", "ptrace", "mount",
                    "unshare", "bpf",
                ],
            }),
            "now": now - timedelta(seconds=6),
        },
    )
    # 4. Insight AgentJob (succeeded, high confidence, 2 attempts)
    a1_insight_usage = {
        "provider": "openai", "model": "gpt-5.4",
        "prompt_chars": 3200, "response_chars": 900,
        "input_tokens_estimated": 800, "output_tokens_estimated": 225,
        "total_tokens_estimated": 1023, "elapsed_seconds": 3.84,
    }
    a1_critic_usage = {
        "provider": "anthropic", "model": "claude-sonnet-4-6",
        "prompt_chars": 4400, "response_chars": 1316,
        "input_tokens_estimated": 1100, "output_tokens_estimated": 329,
        "total_tokens_estimated": 1429, "elapsed_seconds": 5.06,
    }
    a2_insight_usage = {
        "provider": "openai", "model": "gpt-5.4",
        "prompt_chars": 3900, "response_chars": 844,
        "input_tokens_estimated": 975, "output_tokens_estimated": 211,
        "total_tokens_estimated": 1186, "elapsed_seconds": 4.63,
    }
    a2_critic_usage = {
        "provider": "anthropic", "model": "claude-sonnet-4-6",
        "prompt_chars": 4100, "response_chars": 1484,
        "input_tokens_estimated": 1025, "output_tokens_estimated": 371,
        "total_tokens_estimated": 1396, "elapsed_seconds": 4.14,
    }
    total_tokens = sum(u["total_tokens_estimated"] for u in
                       (a1_insight_usage, a1_critic_usage,
                        a2_insight_usage, a2_critic_usage))
    insight_payload = {
        "parent_stat_job_id": str(stat_job_id),
        "attempts": [
            {
                "attempt": 1,
                "insight_usage": a1_insight_usage,
                "critic_usage": a1_critic_usage,
                "verdict": "reject",
                "issues": _A1_CRITIC_ISSUES,
                "reasoning": _A1_CRITIC_REASONING,
                "insight_findings": _A1_INSIGHT_FINDINGS,
            },
            {
                "attempt": 2,
                "insight_usage": a2_insight_usage,
                "critic_usage": a2_critic_usage,
                "verdict": "approve",
                "issues": [],
                "reasoning": _A2_CRITIC_REASONING,
                "insight_findings": _A2_INSIGHT_FINDINGS,
            },
        ],
        "final_insights": _A2_INSIGHT_FINDINGS,
        "confidence": "high",
        "critic_final_objection": None,
        "attempts_used": 2,
        "max_attempts": 3,
    }
    await session.execute(
        _sql_text(
            "INSERT INTO agent_jobs (id, tenant_id, dataset_id, agent_type, status, "
            "started_at, finished_at, cost_tokens, payload, created_at) VALUES "
            "(:id, :tid, :did, 'insight', 'succeeded', :s, :f, :tok, :payload, :now)"
        ),
        {
            "id": str(insight_job_id), "tid": str(tenant_id), "did": str(ds_id),
            "s": now - timedelta(seconds=4), "f": now,
            "tok": total_tokens,
            "payload": json.dumps(insight_payload),
            "now": now - timedelta(seconds=4),
        },
    )
    # 5. AgentLog rows (2 insight, 2 critic)
    log_rows = [
        (
            "insight", "attempt_1",
            {
                "usage": a1_insight_usage,
                "feedback_received": None,
                "parse_error": None,
                "output_finding_count": len(_A1_INSIGHT_FINDINGS["findings"]),
                "raw_text_head": json.dumps(_A1_INSIGHT_FINDINGS)[:1200],
                "force_overstate_active": True,
            },
            now - timedelta(seconds=4),
        ),
        (
            "critic", "attempt_1",
            {
                "usage": a1_critic_usage,
                "verdict": "reject",
                "reasoning": _A1_CRITIC_REASONING,
                "issues": _A1_CRITIC_ISSUES,
                "parse_error": None,
                "raw_text_head": json.dumps({
                    "verdict": "reject",
                    "reasoning": _A1_CRITIC_REASONING,
                    "specific_issues": _A1_CRITIC_ISSUES,
                })[:1200],
            },
            now - timedelta(seconds=3),
        ),
        (
            "insight", "attempt_2",
            {
                "usage": a2_insight_usage,
                "feedback_received": ("The reviewer's specific objections:\n- [1] "
                                     + _A1_CRITIC_ISSUES[0]["problem"]
                                     + "  Fix: " + _A1_CRITIC_ISSUES[0]["suggestion"]),
                "parse_error": None,
                "output_finding_count": len(_A2_INSIGHT_FINDINGS["findings"]),
                "raw_text_head": json.dumps(_A2_INSIGHT_FINDINGS)[:1200],
                "force_overstate_active": False,
            },
            now - timedelta(seconds=2),
        ),
        (
            "critic", "attempt_2",
            {
                "usage": a2_critic_usage,
                "verdict": "approve",
                "reasoning": _A2_CRITIC_REASONING,
                "issues": [],
                "parse_error": None,
                "raw_text_head": json.dumps({
                    "verdict": "approve",
                    "reasoning": _A2_CRITIC_REASONING,
                    "specific_issues": [],
                })[:1200],
            },
            now - timedelta(seconds=1),
        ),
    ]
    for role, step, payload, ts in log_rows:
        await session.execute(
            _sql_text(
                "INSERT INTO agent_logs (id, tenant_id, job_id, agent_role, step, "
                "payload, created_at) VALUES "
                "(:id, :tid, :jid, :role, :step, :payload, :ts)"
            ),
            {
                "id": str(uuid.uuid4()), "tid": str(tenant_id),
                "jid": str(insight_job_id), "role": role, "step": step,
                "payload": json.dumps(payload), "ts": ts,
            },
        )
    # 6. CriticDecision rows
    for verdict, reasoning, blocked, ts in [
        ("blocked", _A1_CRITIC_REASONING, True,  now - timedelta(seconds=3)),
        ("approved", _A2_CRITIC_REASONING, False, now - timedelta(seconds=1)),
    ]:
        await session.execute(
            _sql_text(
                "INSERT INTO critic_decisions (id, tenant_id, job_id, verdict, "
                "reasoning, blocked, created_at) VALUES "
                "(:id, :tid, :jid, :v, :r, :b, :ts)"
            ),
            {
                "id": str(uuid.uuid4()), "tid": str(tenant_id),
                "jid": str(insight_job_id), "v": verdict, "r": reasoning,
                "b": blocked, "ts": ts,
            },
        )
    return True


async def seed_demo_for_all_tenants() -> None:
    """Iterates every existing tenant, adds the demo dataset if missing.
    Uses the BYPASSRLS auth engine because it writes into multiple tenants."""
    async with auth_session() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()
        seeded = 0
        for t in tenants:
            try:
                if await _seed_for_tenant(session, t.id):
                    seeded += 1
            except Exception:  # noqa: BLE001
                logger.exception("failed to seed demo for tenant %s", t.id)
        logger.info("demo seed: created %d new demo datasets across %d tenants",
                    seeded, len(tenants))
