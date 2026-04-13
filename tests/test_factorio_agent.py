import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overwatch.config import Settings
from overwatch.factorio.agent import run_factorio_agent
from overwatch.factorio.executor import SkillExecutor
from overwatch.factorio.models import FactorioPlan, FactorioState, GameAction, GameActionType
from overwatch.factorio.session import FactorioSessionStore


class TestFactorioAgent(unittest.TestCase):
    def test_run_two_steps_mocked_perception_plan(self) -> None:
        st = FactorioState(confidence=0.9, active_gui="none")
        pl = FactorioPlan(
            action=GameAction(type=GameActionType.noop),
            rationale="wait",
        )

        async def run() -> None:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / "f"
                store = FactorioSessionStore(root)
                try:
                    sid = store.create_session(meta={"goal": "x"})
                    settings = Settings(vllm_base_url="http://localhost/v1")
                    ex = SkillExecutor(dry_run=True, max_actions_per_minute=100)
                    fake_png = b"\x89PNG\r\n\x1a\nstub"
                    with (
                        patch(
                            "overwatch.factorio.agent.parse_factorio_state_from_png",
                            new=AsyncMock(return_value=(st, "{}")),
                        ),
                        patch(
                            "overwatch.factorio.agent.plan_next_action",
                            new=AsyncMock(return_value=(pl, "raw")),
                        ),
                    ):
                        n = await run_factorio_agent(
                            settings,
                            store,
                            sid,
                            goal="test goal",
                            tech_tree_text=None,
                            executor=ex,
                            max_steps=2,
                            settle_sec=0.0,
                            capture_fn=lambda: fake_png,
                        )
                    self.assertEqual(n, 2)
                    self.assertEqual(len(store.list_frames(sid)), 2)
                    steps = store.list_agent_steps(sid)
                    self.assertEqual(len(steps), 2)
                    self.assertEqual(steps[0].step_index, 0)
                    self.assertIsNotNone(steps[0].state_json)
                    self.assertIn("noop", steps[0].action_json)
                finally:
                    store.close()

        asyncio.run(run())

    def test_low_confidence_skips_planner(self) -> None:
        st = FactorioState(confidence=0.1, active_gui="unknown")

        async def run() -> None:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / "f"
                store = FactorioSessionStore(root)
                try:
                    sid = store.create_session()
                    settings = Settings(vllm_base_url="http://localhost/v1")
                    ex = SkillExecutor(dry_run=True, max_actions_per_minute=100)
                    plan_mock = AsyncMock(return_value=(None, None))
                    with (
                        patch(
                            "overwatch.factorio.agent.parse_factorio_state_from_png",
                            new=AsyncMock(return_value=(st, "{}")),
                        ),
                        patch(
                            "overwatch.factorio.agent.plan_next_action",
                            new=plan_mock,
                        ),
                    ):
                        await run_factorio_agent(
                            settings,
                            store,
                            sid,
                            goal="g",
                            tech_tree_text=None,
                            executor=ex,
                            max_steps=1,
                            settle_sec=0.0,
                            capture_fn=lambda: b"\x89PNG\r\n\x1a\nx",
                            confidence_threshold=0.25,
                        )
                    plan_mock.assert_not_called()
                    step = store.list_agent_steps(sid)[0]
                    self.assertIn("confidence", step.planner_raw_text or "")
                finally:
                    store.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
