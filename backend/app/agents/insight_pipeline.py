"""Insight-narrative + critic pipeline orchestrator.

Flow:
  attempt 1: insight_agent.generate_insights(feedback=None)
             -> critic_agent.critique(...)
             -> if approve: DONE (status=succeeded)
             -> if reject:  loop with feedback
  attempts 2 & 3: same, feedback = critic's specific issues.
  If still rejected after attempt 3 -> status="succeeded_low_confidence",
    payload.critic_final_objection is set, nothing is hidden.

Persistence:
  - AgentJob row (agent_type="insight") — one per pipeline run.
      status: pending -> running -> succeeded | succeeded_low_confidence | failed
      cost_tokens: sum of all LLM call token estimates for the run.
      payload: {parent_stat_job_id, attempts:[...], final_insights, ...}
  - AgentLog rows — one per LLM call (insight or critic). agent_role +
    step + payload (model, usage, prompt_chars, response_chars).
  - CriticDecision rows — one per critic invocation with verdict + reasoning.
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from ..db import app_session
from ..models import AgentJob, AgentLog, CriticDecision
from .critic_agent import CriticResult, critique
from .insight_agent import InsightResult, generate_insights

logger = logging.getLogger(__name__)

MAX_RETRIES = 2  # 1 initial + 2 retries = 3 attempts total


async def _log_agent_step(
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    agent_role: str,
    step: str,
    payload: dict,
) -> None:
    async with app_session(tenant_id=str(tenant_id)) as session:
        session.add(AgentLog(
            tenant_id=tenant_id,
            job_id=job_id,
            agent_role=agent_role,
            step=step,
            payload=payload,
        ))


async def _log_critic_decision(
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    verdict: str,
    reasoning: str,
    blocked: bool,
) -> None:
    async with app_session(tenant_id=str(tenant_id)) as session:
        session.add(CriticDecision(
            tenant_id=tenant_id,
            job_id=job_id,
            verdict=verdict,
            reasoning=reasoning[:5000],
            blocked=blocked,
        ))


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
            logger.warning("insight job %s vanished before state update", job_id)
            return
        for k, v in fields.items():
            setattr(job, k, v)


async def _increment_cost_tokens(
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    delta: int,
) -> None:
    async with app_session(tenant_id=str(tenant_id)) as session:
        job = (
            await session.execute(select(AgentJob).where(AgentJob.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            return
        job.cost_tokens = int(job.cost_tokens or 0) + int(delta)


async def run_insight_pipeline(
    *,
    tenant_id: uuid.UUID,
    insight_job_id: uuid.UUID,
    parent_stat_job_id: uuid.UUID,
    profile_columns: list[dict],
    stat_code: str,
    stat_result: dict,
    force_overstate_first_attempt: bool = False,
) -> None:
    """Main orchestrator. Persists state as it goes. Never raises."""
    now = lambda: datetime.now(timezone.utc)  # noqa: E731

    attempts_log: list[dict] = []
    last_insight: Optional[InsightResult] = None
    last_critique: Optional[CriticResult] = None
    approved = False

    try:
        await _set_job_state(
            tenant_id, insight_job_id, status="running", started_at=now()
        )
        insight_feedback: Optional[str] = None

        for attempt in range(1, MAX_RETRIES + 2):  # 1, 2, 3
            # -------- 1. Insight generation --------
            overstate_this_time = force_overstate_first_attempt and attempt == 1
            insight = await generate_insights(
                profile_columns=profile_columns,
                stat_result=stat_result,
                job_id=insight_job_id,
                attempt=attempt,
                feedback=insight_feedback,
                force_overstate=overstate_this_time,
            )
            last_insight = insight
            await _log_agent_step(
                tenant_id, insight_job_id,
                agent_role="insight",
                step=f"attempt_{attempt}",
                payload={
                    "usage": insight.usage.to_dict(),
                    "feedback_received": insight_feedback,
                    "parse_error": insight.parse_error,
                    "output_finding_count": len(insight.parsed.get("findings") or []),
                    "raw_text_head": insight.raw_text[:1200],
                    "force_overstate_active": insight.force_overstate_active,
                },
            )
            await _increment_cost_tokens(
                tenant_id, insight_job_id,
                insight.usage.total_tokens_estimated(),
            )

            # -------- 2. Critic --------
            crit = await critique(
                profile_columns=profile_columns,
                stat_code=stat_code,
                stat_result=stat_result,
                insight_findings=insight.parsed,
                job_id=insight_job_id,
                attempt=attempt,
            )
            last_critique = crit
            await _log_agent_step(
                tenant_id, insight_job_id,
                agent_role="critic",
                step=f"attempt_{attempt}",
                payload={
                    "usage": crit.usage.to_dict(),
                    "verdict": crit.verdict,
                    "reasoning": crit.reasoning[:2000],
                    "issues": crit.issues,
                    "parse_error": crit.parse_error,
                    "raw_text_head": crit.raw_text[:1200],
                },
            )
            await _increment_cost_tokens(
                tenant_id, insight_job_id,
                crit.usage.total_tokens_estimated(),
            )
            await _log_critic_decision(
                tenant_id, insight_job_id,
                verdict="approved" if crit.verdict == "approve" else "blocked",
                reasoning=crit.reasoning,
                blocked=(crit.verdict != "approve"),
            )
            attempts_log.append({
                "attempt": attempt,
                "insight_usage": insight.usage.to_dict(),
                "critic_usage": crit.usage.to_dict(),
                "verdict": crit.verdict,
                "issues": crit.issues,
                "reasoning": crit.reasoning,
                "insight_findings": insight.parsed,
            })

            if crit.verdict == "approve":
                approved = True
                break

            # -------- 3. Rejected: route feedback to the right agent --------
            insight_targeted = [i for i in crit.issues
                                if i.get("target") == "insight_agent"]
            stat_targeted = [i for i in crit.issues
                             if i.get("target") == "statistical_agent"]
            # Phase 3 only re-runs the insight agent; statistical re-run
            # would need re-executing the sandbox and is out of scope here.
            # We still record the stat-targeted feedback in the log so it's
            # visible in the audit trail.
            if stat_targeted and not insight_targeted:
                # Nothing productive to do on retry — bail early to
                # low_confidence.
                insight_feedback = crit.feedback_for("insight_agent") or crit.reasoning
                await _log_agent_step(
                    tenant_id, insight_job_id,
                    agent_role="orchestrator",
                    step=f"attempt_{attempt}_bailout",
                    payload={"reason": "critic_targeted_stat_agent_only",
                             "stat_issues": stat_targeted},
                )
                break
            insight_feedback = crit.feedback_for("insight_agent")

        # -------- 4. Final status --------
        if approved:
            final_status = "succeeded"
            final_error = None
        else:
            final_status = "succeeded_low_confidence"
            final_error = None  # not a system failure

        payload = {
            "parent_stat_job_id": str(parent_stat_job_id),
            "attempts": attempts_log,
            "final_insights": (last_insight.parsed if last_insight else None),
            "confidence": "high" if approved else "low",
            "critic_final_objection": (
                None if approved else {
                    "reasoning": last_critique.reasoning if last_critique else "n/a",
                    "issues": last_critique.issues if last_critique else [],
                }
            ),
            "attempts_used": len(attempts_log),
            "max_attempts": MAX_RETRIES + 1,
        }

        await _set_job_state(
            tenant_id, insight_job_id,
            status=final_status,
            finished_at=now(),
            error=final_error,
            payload=payload,
        )

    except Exception as e:  # noqa: BLE001
        logger.exception("insight pipeline crashed for job %s", insight_job_id)
        await _set_job_state(
            tenant_id, insight_job_id,
            status="failed",
            finished_at=now(),
            error=f"{type(e).__name__}: {e}"[:2000],
            payload={
                "parent_stat_job_id": str(parent_stat_job_id),
                "attempts": attempts_log,
            },
        )
