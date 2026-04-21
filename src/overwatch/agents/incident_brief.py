from __future__ import annotations

from typing import Any, cast

from overwatch.agents.adk_skill_runner import run_job_agent_adk
from overwatch.config import Settings
from overwatch.models import AgentKind, IncidentBriefAgentResult

AGENT_INCIDENT_BRIEF_EVENT = "agent_incident_brief"
AGENT_ID = "incident_brief"


async def run_incident_brief_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[IncidentBriefAgentResult | None, dict[str, Any]]:
    """
    Text-only LLM pass: incident-style brief from ``summary`` JSON (ADK ``SkillToolset`` + vLLM via LiteLLM).

    Returns ``(result | None, meta)`` where ``meta`` includes ``attempts``, ``truncated_input``, and optional ``error``.
    """
    r, meta = await run_job_agent_adk(settings, AgentKind.incident_brief, summary)
    return cast(IncidentBriefAgentResult | None, r), meta
