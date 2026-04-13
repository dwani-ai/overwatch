import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.config import Settings
from overwatch.factorio.models import FactorioState
from overwatch.factorio.state_parser import parse_factorio_state_from_png
from overwatch.vllm_client import VllmCallResult


class TestFactorioStateParser(unittest.TestCase):
    def test_disabled_vllm_returns_none(self) -> None:
        async def run() -> None:
            s = Settings(vllm_base_url="")
            st, txt = await parse_factorio_state_from_png(s, b"\x89PNG\r\n\x1a\nx")
            self.assertIsNone(st)
            self.assertIsNone(txt)

        asyncio.run(run())

    def test_parse_from_mock_response(self) -> None:
        payload = {
            "schema_version": "1",
            "tick_or_time_text": "1h 0m",
            "score_text": "1000",
            "researched_technologies": ["automation"],
            "inventory_highlights": ["iron-plate: 40"],
            "active_gui": "research",
            "confidence": 0.85,
            "raw_notes": None,
        }
        fake = VllmCallResult(
            ok=True,
            data={"choices": [{"message": {"content": json.dumps(payload)}}]},
            status_code=200,
        )

        async def run() -> None:
            s = Settings(vllm_base_url="http://localhost/v1", vllm_model="m")
            with patch(
                "overwatch.factorio.state_parser.chat_completion",
                new=AsyncMock(return_value=fake),
            ):
                st, raw = await parse_factorio_state_from_png(s, b"\x89PNG\r\n\x1a\nx")
            self.assertIsNotNone(st)
            assert st is not None
            self.assertEqual(st, FactorioState.model_validate(payload))
            self.assertIsNotNone(raw)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
