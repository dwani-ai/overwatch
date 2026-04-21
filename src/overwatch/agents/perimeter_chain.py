from __future__ import annotations

from typing import Any, cast

from overwatch.agents.adk_skill_runner import run_job_agent_adk
from overwatch.config import Settings
from overwatch.models import AgentKind, PerimeterChainAgentResult

AGENT_PERIMETER_CHAIN_EVENT = "agent_perimeter_chain"
AGENT_ID = "perimeter_chain"


async def run_perimeter_chain_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[PerimeterChainAgentResult | None, dict[str, Any]]:
    r, meta = await run_job_agent_adk(settings, AgentKind.perimeter_chain, summary)
    return cast(PerimeterChainAgentResult | None, r), meta
