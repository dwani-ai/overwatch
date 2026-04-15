import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import ValidationError

from overwatch.factorio.executor import SkillExecutor
from overwatch.factorio.models import GameAction, GameActionType


class TestSkillExecutor(unittest.TestCase):
    def test_dry_run_skill(self) -> None:
        ex = SkillExecutor(dry_run=True, max_actions_per_minute=100)
        keys = ex.execute(GameAction(type=GameActionType.skill, skill="open_research"))
        self.assertEqual(keys, ["t"])

    def test_unknown_skill(self) -> None:
        ex = SkillExecutor(dry_run=True)
        with self.assertRaises(ValueError):
            ex.execute(GameAction(type=GameActionType.skill, skill="nope"))

    def test_action_cap(self) -> None:
        ex = SkillExecutor(dry_run=True, max_actions_per_minute=2)
        ex.execute(GameAction(type=GameActionType.skill, skill="close_menu"))
        ex.execute(GameAction(type=GameActionType.skill, skill="close_menu"))
        with self.assertRaises(RuntimeError):
            ex.execute(GameAction(type=GameActionType.skill, skill="close_menu"))

    def test_dry_run_click(self) -> None:
        ex = SkillExecutor(dry_run=True, max_actions_per_minute=100)
        out = ex.execute(
            GameAction(type=GameActionType.click, click_x=10, click_y=20),
            click_screen_offset=(100, 50),
        )
        self.assertEqual(out, ["click:110,70"])

    def test_click_disabled(self) -> None:
        ex = SkillExecutor(dry_run=True, allow_click=False)
        out = ex.execute(GameAction(type=GameActionType.click, click_x=1, click_y=2))
        self.assertEqual(out, [])

    def test_click_requires_coordinates(self) -> None:
        with self.assertRaises(ValidationError):
            GameAction(type=GameActionType.click)


if __name__ == "__main__":
    unittest.main()
