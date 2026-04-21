from __future__ import annotations

from typing import Any, cast

from overwatch.agents.adk_skill_runner import run_job_agent_adk
from overwatch.config import Settings
from overwatch.models import AgentKind, ComplianceBriefAgentResult

AGENT_COMPLIANCE_BRIEF_EVENT = "agent_compliance_brief"
AGENT_ID = "compliance_brief"


async def run_compliance_brief_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[ComplianceBriefAgentResult | None, dict[str, Any]]:
    r, meta = await run_job_agent_adk(settings, AgentKind.compliance_brief, summary)
    return cast(ComplianceBriefAgentResult | None, r), meta
