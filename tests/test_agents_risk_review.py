from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from overwatch.agents.risk_review import run_risk_review_agent
from overwatch.config import Settings
from overwatch.vllm_client import VllmCallResult


class TestRiskReviewAgent(unittest.IsolatedAsyncioTestCase):
    async def test_empty_base_url_skips_llm(self) -> None:
        settings = Settings(vllm_base_url="")
        result, meta = await run_risk_review_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNone(result)
        self.assertIn("error", meta)

    async def test_parses_valid_json_response(self) -> None:
        settings = Settings(vllm_base_url="http://test/v1", vllm_model="gemma4")
        content = (
            '{"schema_version":"1","overall_risk":"medium","requires_immediate_review":false,'
            '"risk_factors":["forklift near pedestrian"],"operator_notes":"Monitor zone A.",'
            '"mitigations_suggested":["slow traffic"]}'
        )
        fake = VllmCallResult(ok=True, data={"choices": [{"message": {"content": content}}]})
        with patch("overwatch.agents.risk_review.chat_completion", new_callable=AsyncMock, return_value=fake):
            result, meta = await run_risk_review_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.overall_risk, "medium")
        self.assertTrue(any("forklift" in x for x in result.risk_factors))
        self.assertEqual(meta.get("attempts"), 1)
