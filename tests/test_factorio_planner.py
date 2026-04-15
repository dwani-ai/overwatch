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

    def test_parse_click_action(self) -> None:
        payload = {
            "schema_version": "1",
            "rationale": "mine tile",
            "action": {"type": "click", "click_x": 400, "click_y": 300},
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
                plan, _ = await plan_next_action(
                    s,
                    goal="g",
                    state=st,
                    capture_width=800,
                    capture_height=600,
                )
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan.action.type, GameActionType.click)
            self.assertEqual(plan.action.click_x, 400)
            self.assertEqual(plan.action.click_y, 300)

        asyncio.run(run())

    def test_clamp_click_in_planner_response(self) -> None:
        from overwatch.factorio.planner import clamp_click_to_capture

        a = GameAction(type=GameActionType.click, click_x=10_000, click_y=1)
        b = clamp_click_to_capture(a, 100, 50)
        self.assertEqual(b.click_x, 99)
        self.assertEqual(b.click_y, 1)


if __name__ == "__main__":
    unittest.main()
