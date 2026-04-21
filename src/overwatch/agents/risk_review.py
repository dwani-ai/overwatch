from __future__ import annotations

from typing import Any, cast

from overwatch.agents.adk_skill_runner import run_job_agent_adk
from overwatch.config import Settings
from overwatch.models import AgentKind, RiskReviewAgentResult

AGENT_RISK_REVIEW_EVENT = "agent_risk_review"
AGENT_ID = "risk_review"


async def run_risk_review_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[RiskReviewAgentResult | None, dict[str, Any]]:
    """
    Text-only LLM risk triage over ``summary`` JSON (ADK ``SkillToolset`` + vLLM via LiteLLM).

    Returns ``(result | None, meta)`` where ``meta`` includes ``attempts``, ``truncated_input``, and optional ``error``.
    """
    r, meta = await run_job_agent_adk(settings, AgentKind.risk_review, summary)
    return cast(RiskReviewAgentResult | None, r), meta
