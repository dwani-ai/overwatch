from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from overwatch.agents.incident_brief import run_incident_brief_agent
from overwatch.config import Settings
from overwatch.models import IncidentBriefAgentResult


class TestIncidentBriefAgent(unittest.IsolatedAsyncioTestCase):
    async def test_empty_base_url_skips_llm(self) -> None:
        settings = Settings(vllm_base_url="")
        result, meta = await run_incident_brief_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNone(result)
        self.assertIn("error", meta)

    async def test_returns_result_from_adk_runner(self) -> None:
        settings = Settings(vllm_base_url="http://test/v1", vllm_model="gemma4")
        ok = IncidentBriefAgentResult(
            narrative="Quiet shift.",
            key_moments=["a"],
            situational_factors=[],
            suggested_followups=[],
        )
        with patch(
            "overwatch.agents.incident_brief.run_job_agent_adk",
            new_callable=AsyncMock,
            return_value=(ok, {"attempts": 1, "truncated_input": False, "model": "gemma4"}),
        ):
            result, meta = await run_incident_brief_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.narrative, "Quiet shift.")
        self.assertEqual(meta.get("attempts"), 1)
