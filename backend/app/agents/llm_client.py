"""LLM client helpers used by insight + critic agents.

Wraps emergentintegrations so we can:
    - honor the per-agent model configured via env (INSIGHT_AGENT_MODEL /
      CRITIC_AGENT_MODEL, "provider/model" format)
    - capture per-call usage (input/output token estimates + chars) for
      tenant-level accounting, independent of whose LLM key is billing
    - be swapped for a different SDK later (Groq / DeepSeek / etc) by
      changing THIS file only; agents call ``chat_once()`` and nothing else
"""
from __future__ import annotations
import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Optional

from emergentintegrations.llm.chat import (  # type: ignore
    LlmChat, UserMessage, TextDelta, StreamDone,
)

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class LlmUsage:
    provider: str
    model: str
    prompt_chars: int
    response_chars: int
    input_tokens_estimated: int
    output_tokens_estimated: int
    elapsed_seconds: float

    def total_tokens_estimated(self) -> int:
        return self.input_tokens_estimated + self.output_tokens_estimated

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_chars": self.prompt_chars,
            "response_chars": self.response_chars,
            "input_tokens_estimated": self.input_tokens_estimated,
            "output_tokens_estimated": self.output_tokens_estimated,
            "total_tokens_estimated": self.total_tokens_estimated(),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


@dataclass
class LlmReply:
    text: str
    usage: LlmUsage


def parse_model_spec(spec: str) -> tuple[str, str]:
    """'anthropic/claude-sonnet-4-6' -> ('anthropic', 'claude-sonnet-4-6')."""
    if "/" not in spec:
        raise ValueError(
            f"Invalid model spec {spec!r}. Expected 'provider/model_name'."
        )
    provider, model = spec.split("/", 1)
    provider = provider.strip().lower()
    model = model.strip()
    if provider not in {"openai", "anthropic", "gemini"}:
        raise ValueError(
            f"Unknown provider {provider!r}. Supported: openai | anthropic | gemini."
        )
    return provider, model


def _estimate_tokens(chars: int) -> int:
    # ~4 chars/token is the widely-quoted rule for English. Good enough for
    # tenant accounting; exact numbers require provider-side usage which
    # streaming doesn't reliably expose.
    return int(math.ceil(chars / 4))


async def chat_once(
    *,
    model_spec: str,
    system_message: str,
    user_message: str,
    session_id: str,
    max_output_chars: int = 20_000,
) -> LlmReply:
    """Single-turn, single-message LLM call. No history is kept."""
    settings = get_settings()
    if not settings.emergent_llm_key:
        raise RuntimeError(
            "EMERGENT_LLM_KEY is not configured; cannot make LLM calls."
        )
    provider, model = parse_model_spec(model_spec)

    chat = LlmChat(
        api_key=settings.emergent_llm_key,
        session_id=session_id,
        system_message=system_message,
    ).with_model(provider, model)

    prompt_chars = len(system_message) + len(user_message)
    started = asyncio.get_event_loop().time()
    accum: list[str] = []
    total = 0

    try:
        async for ev in chat.stream_message(UserMessage(text=user_message)):
            if isinstance(ev, TextDelta):
                accum.append(ev.content)
                total += len(ev.content)
                if total > max_output_chars:
                    # Truncate defensively; the caller can't afford to store
                    # arbitrarily large payloads in JSONB.
                    logger.warning("LLM output truncated at %d chars", max_output_chars)
                    break
            elif isinstance(ev, StreamDone):
                break
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"LLM call failed ({provider}/{model}): {e}") from e

    elapsed = asyncio.get_event_loop().time() - started
    text = "".join(accum)
    usage = LlmUsage(
        provider=provider,
        model=model,
        prompt_chars=prompt_chars,
        response_chars=len(text),
        input_tokens_estimated=_estimate_tokens(prompt_chars),
        output_tokens_estimated=_estimate_tokens(len(text)),
        elapsed_seconds=elapsed,
    )
    return LlmReply(text=text, usage=usage)
