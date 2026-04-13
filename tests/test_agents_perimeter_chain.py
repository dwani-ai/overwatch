from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from overwatch.agents.perimeter_chain import run_perimeter_chain_agent
from overwatch.config import Settings
from overwatch.vllm_client import VllmCallResult


class TestPerimeterChainAgent(unittest.IsolatedAsyncioTestCase):
    async def test_empty_base_url_skips_llm(self) -> None:
        settings = Settings(vllm_base_url="")
        result, meta = await run_perimeter_chain_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNone(result)
        self.assertIn("error", meta)

    async def test_parses_valid_json_response(self) -> None:
        settings = Settings(vllm_base_url="http://test/v1", vllm_model="gemma4")
        content = (
            '{"schema_version":"1","chain_narrative":"Gate idle.","key_events":["a"],'
            '"zones_or_segments":["dock"],"follow_up_checks":[]}'
        )
        fake = VllmCallResult(ok=True, data={"choices": [{"message": {"content": content}}]})
        with patch("overwatch.agents.perimeter_chain.chat_completion", new_callable=AsyncMock, return_value=fake):
            result, meta = await run_perimeter_chain_agent(settings, {"schema_version": "1", "chunk_analyses": []})
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("dock", result.zones_or_segments)
        self.assertEqual(meta.get("attempts"), 1)
