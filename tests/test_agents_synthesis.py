from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from overwatch.agents.synthesis import run_synthesis_agent
from overwatch.config import Settings
from overwatch.vllm_client import VllmCallResult


class TestSynthesisAgent(unittest.IsolatedAsyncioTestCase):
    async def test_empty_base_url_skips_llm(self) -> None:
        settings = Settings(vllm_base_url="")
        result, meta = await run_synthesis_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNone(result)
        self.assertIn("error", meta)

    async def test_parses_valid_json_response(self) -> None:
        settings = Settings(vllm_base_url="http://test/v1", vllm_model="gemma4")
        content = (
            '{"schema_version":"1","executive_summary":"All quiet.","key_observations":["a"],'
            '"security_highlights":[],"logistics_highlights":[],"attendance_summary":"",'
            '"recommended_actions":[]}'
        )
        fake = VllmCallResult(ok=True, data={"choices": [{"message": {"content": content}}]})
        with patch("overwatch.agents.synthesis.chat_completion", new_callable=AsyncMock, return_value=fake):
            result, meta = await run_synthesis_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.executive_summary, "All quiet.")
        self.assertEqual(meta.get("attempts"), 1)
