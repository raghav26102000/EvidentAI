"""Critic Agent — runs in an INDEPENDENT LLM context from insight agent.

Two checks per invocation:
  1. Were the statistical tests appropriate for the data shape?
     e.g. Pearson on non-numeric columns, or on columns with <20 rows,
     or on categorical variables treated as numeric.
  2. Are the insight-narrative claims factually supported by the raw
     statistical output — not exaggerated or misstated?
     e.g. calling r=0.05 "notable", or calling 2 outliers "a pattern".

Output STRICT JSON:
{
  "verdict": "approve" | "reject",
  "reasoning": "<one paragraph explaining the verdict>",
  "specific_issues": [
    { "target": "statistical_agent" | "insight_agent",
      "finding_rank_or_test": <int or str>,
      "problem": "<what is wrong>",
      "suggestion": "<what to change>" }
  ]
}

`target` selects which upstream agent the retry should go back to.
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


_SYSTEM_PROMPT = """You are the EvidentAI Critic Agent — an INDEPENDENT auditor.

You are given:
  1. The dataset PROFILE (columns with types, cardinality, null counts, size).
  2. The STAT CODE the statistical agent generated and executed.
  3. The raw STAT RESULT (numeric output from that code).
  4. The INSIGHT AGENT'S findings, which are prose claims about the STAT RESULT.

You must audit TWO independent things:

  A. Were the statistical tests appropriate for the data shape?
     Flag things like: Pearson on non-numeric columns, correlation reported
     on n < 20 rows, IQR outliers described but count is essentially zero,
     using groupby on high-cardinality string columns, etc.

  B. Are the insight-narrative claims factually supported by the numbers?
     Flag things like:
       * describing |r| < 0.1 as "notable", "meaningful", or "strong"
       * describing |r| in 0.1–0.3 as anything above "weak"
       * asserting a correlation that DOES NOT appear in the STAT RESULT
       * declaring outliers "a pattern" when the count < 1% of rows
       * confusing correlation with causation ("X causes Y")

Approve if — and only if — both A and B pass. If either fails, reject and
list every issue found under `specific_issues`.

Hard rules:
  * Output STRICT JSON only. No prose outside JSON, no code fences.
  * Every element of `specific_issues` must have `target` set to either
    "statistical_agent" (if the test choice was wrong) or
    "insight_agent" (if the write-up misrepresents the numbers).
  * Be terse. Reviewer prose belongs in `reasoning`, one paragraph max.
"""


@dataclass
class CriticResult:
    parsed: dict           # {"verdict":..., "reasoning":..., "specific_issues":[...]}
    raw_text: str
    usage: LlmUsage
    parse_error: Optional[str] = None

    @property
    def verdict(self) -> str:
        v = (self.parsed.get("verdict") or "").strip().lower()
        return v if v in {"approve", "reject"} else "reject"

    @property
    def reasoning(self) -> str:
        return self.parsed.get("reasoning") or self.parse_error or "no reasoning returned"

    @property
    def issues(self) -> list[dict]:
        return self.parsed.get("specific_issues") or []

    def feedback_for(self, target: str) -> str:
        """Formatted feedback string to hand back to insight or statistical
        agent on retry. Only issues with matching target are included."""
        matched = [i for i in self.issues if i.get("target") == target]
        if not matched:
            # No target-specific issues — feed the general reasoning back so
            # the retry has SOMETHING to act on.
            return self.reasoning
        lines: list[str] = []
        for i in matched:
            problem = i.get("problem", "").strip()
            suggestion = i.get("suggestion", "").strip()
            ref = i.get("finding_rank_or_test", "")
            lines.append(f"- [{ref}] {problem}  Fix: {suggestion}")
        return "The reviewer's specific objections:\n" + "\n".join(lines)


def _build_user_message(
    profile_columns, stat_code: str, stat_result: dict, insight_findings: dict
) -> str:
    return (
        "PROFILE:\n" + json.dumps(profile_columns, default=str) +
        "\n\nSTAT CODE:\n" + stat_code +
        "\n\nSTAT RESULT:\n" + json.dumps(stat_result, default=str) +
        "\n\nINSIGHT FINDINGS:\n" + json.dumps(insight_findings, default=str) +
        "\n\nAudit both A and B above. Return your JSON verdict."
    )


async def critique(
    *,
    profile_columns: list[dict],
    stat_code: str,
    stat_result: dict,
    insight_findings: dict,
    job_id: uuid.UUID,
    attempt: int,
) -> CriticResult:
    settings = get_settings()
    session_id = f"critic-{job_id}-attempt-{attempt}"
    reply: LlmReply = await chat_once(
        model_spec=settings.critic_agent_model,
        system_message=_SYSTEM_PROMPT,
        user_message=_build_user_message(
            profile_columns, stat_code, stat_result, insight_findings
        ),
        session_id=session_id,
    )
    parsed, parse_err = _extract_json(reply.text)
    return CriticResult(
        parsed=parsed or {"verdict": "reject",
                           "reasoning": f"unparseable critic output: {parse_err}",
                           "specific_issues": []},
        raw_text=reply.text,
        usage=reply.usage,
        parse_error=parse_err,
    )


def _extract_json(text: str) -> tuple[Optional[dict], Optional[str]]:
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
    return None, "could not parse JSON from critic output"


def _strip_code_fence(s: str) -> str:
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()
