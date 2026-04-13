import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.config import Settings
from overwatch.factorio.eval_manifest import load_parser_eval_cases
from overwatch.factorio.state_parser import parse_factorio_state_from_png
from overwatch.vllm_client import VllmCallResult


REPO = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO / "evals" / "factorio_parser"


class TestFactorioParserEvalManifest(unittest.TestCase):
    def test_load_cases(self) -> None:
        cases = load_parser_eval_cases(EVAL_DIR)
        self.assertTrue(len(cases) >= 1)
        minimal = next(c for c in cases if c.id == "minimal")
        self.assertTrue(minimal.image_path.is_file())
        self.assertEqual(minimal.expected_state.active_gui, "none")

    def test_manifest_roundtrip_with_mock_vllm(self) -> None:
        import asyncio

        cases = load_parser_eval_cases(EVAL_DIR)
        for case in cases:
            expected_json = case.expected_state.model_dump_json()
            fake = VllmCallResult(
                ok=True,
                data={"choices": [{"message": {"content": expected_json}}]},
                status_code=200,
            )

            async def run_one() -> None:
                s = Settings(vllm_base_url="http://localhost/v1", vllm_model="m")
                png = case.image_path.read_bytes()
                with patch(
                    "overwatch.factorio.state_parser.chat_completion",
                    new=AsyncMock(return_value=fake),
                ):
                    st, _ = await parse_factorio_state_from_png(s, png)
                self.assertIsNotNone(st)
                assert st is not None
                self.assertEqual(st.model_dump(), case.expected_state.model_dump())

            asyncio.run(run_one())


if __name__ == "__main__":
    unittest.main()
