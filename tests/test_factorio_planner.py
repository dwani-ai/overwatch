import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.config import Settings
from overwatch.factorio.models import FactorioState, GameAction, GameActionType
from overwatch.factorio.planner import plan_next_action
from overwatch.vllm_client import VllmCallResult


class TestFactorioPlanner(unittest.TestCase):
    def test_disabled_vllm(self) -> None:
        async def run() -> None:
            s = Settings(vllm_base_url="")
            st = FactorioState(confidence=0.9, active_gui="none")
            plan, raw = await plan_next_action(s, goal="mine iron", state=st)
            self.assertIsNone(plan)
            self.assertIsNone(raw)

        asyncio.run(run())

    def test_parse_plan(self) -> None:
        payload = {
            "schema_version": "1",
            "rationale": "check research",
            "action": {"type": "skill", "skill": "open_research"},
        }
        fake = VllmCallResult(
            ok=True,
            data={"choices": [{"message": {"content": json.dumps(payload)}}]},
            status_code=200,
        )

        async def run() -> None:
            s = Settings(vllm_base_url="http://localhost/v1", vllm_model="m")
            st = FactorioState(confidence=0.9)
            with patch(
                "overwatch.factorio.planner.chat_completion",
                new=AsyncMock(return_value=fake),
            ):
                plan, raw = await plan_next_action(s, goal="g", state=st)
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan.action.type, GameActionType.skill)
            self.assertEqual(plan.action.skill, "open_research")
            self.assertIsNotNone(raw)

        asyncio.run(run())

    def test_unknown_skill_sanitized(self) -> None:
        payload = {
            "schema_version": "1",
            "rationale": "bad",
            "action": {"type": "skill", "skill": "not_a_real_skill"},
        }
        fake = VllmCallResult(
            ok=True,
            data={"choices": [{"message": {"content": json.dumps(payload)}}]},
            status_code=200,
        )

        async def run() -> None:
            s = Settings(vllm_base_url="http://localhost/v1", vllm_model="m")
            st = FactorioState(confidence=0.9)
            with patch(
                "overwatch.factorio.planner.chat_completion",
                new=AsyncMock(return_value=fake),
            ):
                plan, _ = await plan_next_action(s, goal="g", state=st)
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan.action.type, GameActionType.noop)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
