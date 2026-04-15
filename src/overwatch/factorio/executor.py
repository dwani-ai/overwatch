from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from overwatch.factorio.models import GameAction, GameActionType
from overwatch.factorio.skills import SKILL_KEY_SEQUENCES

logger = logging.getLogger(__name__)


@dataclass
class SkillExecutor:
    """
    Execute :class:`GameAction` with a per-minute cap. Default ``dry_run=True`` logs only.

    When ``dry_run=False``, uses ``pyautogui`` if available; otherwise raises ``RuntimeError``.
    """

    max_actions_per_minute: int = 30
    dry_run: bool = True
    allow_click: bool = True
    _window_start: float = field(default_factory=time.monotonic, repr=False)
    _count_in_window: int = field(default=0, repr=False)

    def _reset_window_if_needed(self) -> None:
        now = time.monotonic()
        if now - self._window_start >= 60.0:
            self._window_start = now
            self._count_in_window = 0

    def _bump_and_check_cap(self) -> None:
        self._reset_window_if_needed()
        if self._count_in_window >= self.max_actions_per_minute:
            raise RuntimeError(
                f"action cap exceeded: {self.max_actions_per_minute} actions per minute"
            )
        self._count_in_window += 1

    def _keys_for_action(self, action: GameAction) -> list[str]:
        if action.type == GameActionType.noop:
            return []
        if action.type == GameActionType.skill:
            if not action.skill:
                raise ValueError("skill action requires skill name")
            seq = SKILL_KEY_SEQUENCES.get(action.skill)
            if seq is None:
                raise ValueError(f"unknown skill: {action.skill!r}; known: {sorted(SKILL_KEY_SEQUENCES)}")
            return list(seq)
        if action.type == GameActionType.key:
            if not action.key:
                raise ValueError("key action requires key")
            return [action.key]
        if action.type == GameActionType.keys:
            if not action.keys:
                raise ValueError("keys action requires keys list")
            return list(action.keys)
        if action.type == GameActionType.click:
            return []
        raise ValueError(f"unsupported action type: {action.type}")

    def execute(
        self,
        action: GameAction,
        *,
        click_screen_offset: tuple[int, int] = (0, 0),
    ) -> list[str]:
        if action.type == GameActionType.noop:
            logger.info("executor noop: %s", action.model_dump())
            return []

        if action.type == GameActionType.click:
            if not self.allow_click:
                logger.warning("executor: click action ignored (allow_click=False)")
                return []
            if action.click_x is None or action.click_y is None:
                raise ValueError("click requires click_x and click_y")
            ox, oy = click_screen_offset
            sx, sy = ox + action.click_x, oy + action.click_y
            self._bump_and_check_cap()
            if self.dry_run:
                logger.info("executor dry_run would click screen (%s, %s) png-relative (%s, %s)", sx, sy, action.click_x, action.click_y)
                return [f"click:{sx},{sy}"]
            try:
                import pyautogui
            except ImportError as e:
                raise RuntimeError(
                    "pyautogui is not installed; keep dry_run=True or pip install pyautogui"
                ) from e
            pyautogui.click(sx, sy)
            logger.info("executor clicked screen (%s, %s)", sx, sy)
            return [f"click:{sx},{sy}"]

        keys = self._keys_for_action(action)
        if not keys:
            logger.info("executor noop: %s", action.model_dump())
            return []

        self._bump_and_check_cap()

        if self.dry_run:
            logger.info("executor dry_run would press: %s", keys)
            return keys

        try:
            import pyautogui
        except ImportError as e:
            raise RuntimeError(
                "pyautogui is not installed; keep dry_run=True or pip install pyautogui"
            ) from e

        for k in keys:
            pyautogui.press(k)
        logger.info("executor pressed: %s", keys)
        return keys
