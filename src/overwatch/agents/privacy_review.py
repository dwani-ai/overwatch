from __future__ import annotations

from typing import Any, cast

from overwatch.agents.adk_skill_runner import run_job_agent_adk
from overwatch.config import Settings
from overwatch.models import AgentKind, PrivacyReviewAgentResult

AGENT_PRIVACY_REVIEW_EVENT = "agent_privacy_review"
AGENT_ID = "privacy_review"


async def run_privacy_review_agent(
    settings: Settings,
    summary: dict[str, Any],
) -> tuple[PrivacyReviewAgentResult | None, dict[str, Any]]:
    r, meta = await run_job_agent_adk(settings, AgentKind.privacy_review, summary)
    return cast(PrivacyReviewAgentResult | None, r), meta
