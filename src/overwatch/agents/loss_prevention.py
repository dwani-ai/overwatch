from __future__ import annotations

from typing import Any, cast

from overwatch.agents.adk_skill_runner import run_job_agent_adk
from overwatch.config import Settings
from overwatch.models import AgentKind, LossPreventionAgentResult

AGENT_LOSS_PREVENTION_EVENT = "agent_loss_prevention"
AGENT_ID = "loss_prevention"


async def run_loss_prevention_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[LossPreventionAgentResult | None, dict[str, Any]]:
    r, meta = await run_job_agent_adk(settings, AgentKind.loss_prevention, summary)
    return cast(LossPreventionAgentResult | None, r), meta
