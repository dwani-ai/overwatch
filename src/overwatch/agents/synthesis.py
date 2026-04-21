from __future__ import annotations

from typing import Any, cast

from overwatch.agents.adk_skill_runner import run_job_agent_adk
from overwatch.config import Settings
from overwatch.models import AgentKind, SynthesisAgentResult

AGENT_SYNTHESIS_EVENT = "agent_synthesis"
AGENT_ID = "synthesis"


async def run_synthesis_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[SynthesisAgentResult | None, dict[str, Any]]:
    """
    Text-only LLM pass over ``summary`` JSON (ADK ``SkillToolset`` + vLLM via LiteLLM).

    Returns ``(result | None, meta)`` where ``meta`` includes ``attempts``, ``truncated_input``, and optional ``error``.
    """
    r, meta = await run_job_agent_adk(settings, AgentKind.synthesis, summary)
    return cast(SynthesisAgentResult | None, r), meta
