from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from overwatch.agents.privacy_review import run_privacy_review_agent
from overwatch.config import Settings
from overwatch.vllm_client import VllmCallResult


class TestPrivacyReviewAgent(unittest.IsolatedAsyncioTestCase):
    async def test_empty_base_url_skips_llm(self) -> None:
        settings = Settings(vllm_base_url="")
        result, meta = await run_privacy_review_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNone(result)
        self.assertIn("error", meta)

    async def test_parses_valid_json_response(self) -> None:
        settings = Settings(vllm_base_url="http://test/v1", vllm_model="gemma4")
        content = (
            '{"schema_version":"1","overall_privacy_risk":"low","identity_inference_risks":[],'
            '"sensitive_descriptors":[],"safe_output_guidance":["x"],"summary":"fine"}'
        )
        fake = VllmCallResult(ok=True, data={"choices": [{"message": {"content": content}}]})
        with patch("overwatch.agents.privacy_review.chat_completion", new_callable=AsyncMock, return_value=fake):
            result, meta = await run_privacy_review_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.overall_privacy_risk, "low")
        self.assertEqual(meta.get("attempts"), 1)
