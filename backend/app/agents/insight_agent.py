"""Insight Narrative Agent.

Input : dataset profile + completed statistical-agent result (correlations,
        outliers, etc).
Output: {"findings": [{"rank": int, "title": str, "claim": str,
                       "importance_score": float, "backed_by": [<test names>],
                       "evidence": {...}}], ...}

Findings are ranked most-significant-first. Each claim MUST cite the
numeric evidence it relies on so the critic agent can verify.

Model configured via settings.insight_agent_model. Independent
session_id per call; no cross-agent history.
"""
from __future__ import annotations
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from ..config import get_settings
from .llm_client import LlmReply, LlmUsage, chat_once

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the EvidentAI Insight Narrative Agent.

You are given:
  1. A dataset PROFILE (columns with types, cardinality, null counts).
  2. The RESULT of a statistical analysis: correlation matrices, IQR outlier
     detections, cohort mean comparisons, and/or time-trend regressions.

Your job: produce a RANKED, plain-language write-up of the most important
findings for a business audience, most significant first.

Hard rules — read carefully:
  * Do NOT invent numbers. Every claim must be traceable to a specific
    value in the RESULT.
  * Do NOT overstate. If |r| < 0.3 say "weak", if 0.3-0.7 say "moderate",
    if > 0.7 say "strong". If |r| < 0.1 call the relationship "essentially
    absent" — do NOT call it "notable" or "meaningful".
  * If an outlier count is small (< 1% of rows) call it "isolated"; do NOT
    describe it as a pattern.
  * Output STRICT JSON only. No prose, no code fences, no leading text.
    A downstream parser will call json.loads() on your entire output.

Schema:
{
  "findings": [
    {
      "rank": 1,
      "title": "<short headline, <= 80 chars>",
      "claim": "<one plain-language sentence>",
      "importance_score": <float 0..1>,
      "backed_by": ["<test name like 'pearson_correlation' or 'iqr_outliers'>", ...],
      "evidence": { "<key>": "<value or number cited from RESULT>" }
    }
  ],
  "summary": "<one short paragraph, <= 400 chars>"
}
"""

_RETRY_INSTRUCTION_HEADER = (
    "The previous version of your findings was REJECTED by an independent "
    "reviewer. Their specific objections are below. Address every objection. "
    "Do NOT ignore any of them. Do NOT weaken your language beyond what "
    "the numbers demand.\n\n"
    "Reviewer objections:\n"
)


@dataclass
class InsightResult:
    parsed: dict            # {"findings":[...], "summary": str}
    raw_text: str
    usage: LlmUsage
    parse_error: Optional[str] = None
    force_overstate_active: bool = False


def _build_user_message(profile_columns, stat_result, feedback: Optional[str]) -> str:
    parts: list[str] = []
    if feedback:
        parts.append(_RETRY_INSTRUCTION_HEADER + feedback + "\n")
    parts.append("PROFILE:")
    parts.append(json.dumps(profile_columns, default=str))
    parts.append("\nSTATISTICAL RESULT:")
    parts.append(json.dumps(stat_result, default=str))
    parts.append(
        "\nProduce the JSON per the schema. Rank findings by importance "
        "(strongest correlations, most outliers relative to n) descending."
    )
    return "\n".join(parts)


async def generate_insights(
    *,
    profile_columns: list[dict],
    stat_result: dict,
    job_id: uuid.UUID,
    attempt: int,
    feedback: Optional[str] = None,
    force_overstate: bool = False,
) -> InsightResult:
    """Call the LLM once and parse its JSON output.

    ``force_overstate`` is a TESTING-ONLY hook: when True, a fabricated
    over-claim is inserted into the parsed output before it's returned,
    so we can prove the critic actually catches and rejects it. This
    hook is off by default and never touched by the HTTP endpoint.
    """
    settings = get_settings()
    session_id = f"insight-{job_id}-attempt-{attempt}"
    user_msg = _build_user_message(profile_columns, stat_result, feedback)
    reply: LlmReply = await chat_once(
        model_spec=settings.insight_agent_model,
        system_message=_SYSTEM_PROMPT,
        user_message=user_msg,
        session_id=session_id,
    )
    parsed, parse_err = _extract_json(reply.text)

    if force_overstate and parsed is not None:
        # Inject an obviously-overstated finding for the critic to catch.
        # Only used by /tests/insight_proof.py to demonstrate the reject
        # path — never by production code.
        overstated = {
            "rank": 0,
            "title": "TESTING OVERSTATEMENT: z is near-perfectly correlated with x",
            "claim": (
                "The variable z shows a near-perfect positive correlation "
                "with x (r > 0.95), indicating a very strong linear dependence."
            ),
            "importance_score": 0.99,
            "backed_by": ["pearson_correlation"],
            "evidence": {"r_xz_claimed": 0.97},
        }
        parsed.setdefault("findings", []).insert(0, overstated)
        for i, f in enumerate(parsed["findings"], start=1):
            f["rank"] = i

    return InsightResult(
        parsed=parsed or {"findings": [], "summary": ""},
        raw_text=reply.text,
        usage=reply.usage,
        parse_error=parse_err,
        force_overstate_active=force_overstate,
    )


def _extract_json(text: str) -> tuple[Optional[dict], Optional[str]]:
    """Robust extraction. Tries verbatim first, then strips code fences."""
    stripped = text.strip()
    for candidate in (stripped, _strip_code_fence(stripped)):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj, None
        except json.JSONDecodeError:
            continue
    return None, f"could not parse JSON from {len(text)} chars of model output"


def _strip_code_fence(s: str) -> str:
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()
