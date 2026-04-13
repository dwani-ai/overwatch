from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from overwatch.config import Settings
from overwatch.factorio.capture import capture_screen_png_with_offset, png_screen_dimensions
from overwatch.factorio.executor import SkillExecutor
from overwatch.factorio.models import FactorioPlan, FactorioState, GameAction, GameActionType
from overwatch.factorio.planner import plan_next_action
from overwatch.factorio.session import FactorioSessionStore
from overwatch.factorio.state_parser import parse_factorio_state_from_png

logger = logging.getLogger(__name__)


async def run_factorio_agent(
    settings: Settings,
    store: FactorioSessionStore,
    session_id: str,
    *,
    goal: str,
    tech_tree_text: str | None,
    executor: SkillExecutor,
    max_steps: int,
    settle_sec: float | None = None,
    monitor: int = 1,
    capture_fn: Callable[[], bytes] | None = None,
    stop_event: asyncio.Event | None = None,
    confidence_threshold: float | None = None,
) -> int:
    """
    Observe → parse → plan → persist → execute → settle, up to ``max_steps`` iterations.

    Returns the number of iterations completed (each iteration performs one capture).

    ``capture_fn`` overrides default monitor capture (for tests). ``stop_event`` aborts between steps.
    """
    settle = settings.factorio_settle_sec if settle_sec is None else settle_sec
    thresh = (
        settings.factorio_confidence_threshold
        if confidence_threshold is None
        else confidence_threshold
    )

    def _capture_pack() -> tuple[bytes, int, int]:
        if capture_fn is not None:
            return capture_fn(), 0, 0
        return capture_screen_png_with_offset(monitor=monitor)

    completed = 0
    frames = store.list_frames(session_id)
    step0 = len(frames)

    for i in range(max_steps):
        if stop_event is not None and stop_event.is_set():
            logger.info("factorio agent: stop_event set, exiting after %s steps", completed)
            break

        step_index = step0 + i
        png, click_off_x, click_off_y = await asyncio.to_thread(_capture_pack)
        frame_rec = store.append_frame(session_id, step_index, png)

        dims = png_screen_dimensions(png)
        cap_w, cap_h = (dims[0], dims[1]) if dims else (None, None)

        state, parse_raw = await parse_factorio_state_from_png(
            settings,
            png,
            tech_tree_context=tech_tree_text,
        )

        err: str | None = None
        planner_raw: str | None = parse_raw
        action: GameAction
        plan: FactorioPlan | None = None

        if state is None:
            err = "state parse failed or vLLM disabled"
            action = GameAction(type=GameActionType.noop)
            planner_raw = (planner_raw or "") + "\n[guard: no parsed state]"
        elif state.confidence < thresh:
            action = GameAction(type=GameActionType.noop)
            planner_raw = (
                (parse_raw or "")
                + f"\n[guard: confidence {state.confidence} < {thresh}]"
            )
        else:
            plan, ptext = await plan_next_action(
                settings,
                goal=goal,
                state=state,
                tech_tree_text=tech_tree_text,
                capture_width=cap_w,
                capture_height=cap_h,
            )
            planner_raw = ptext
            if plan is not None:
                action = plan.action
            else:
                err = err or "planner failed or returned invalid JSON"
                action = GameAction(type=GameActionType.noop)

        state_json = state.model_dump_json() if state is not None else None
        try:
            executor.execute(action, click_screen_offset=(click_off_x, click_off_y))
        except Exception as e:
            logger.warning("executor failed at step %s: %s", step_index, e)
            err = f"{err}; executor: {e}" if err else f"executor: {e}"

        store.append_agent_step(
            session_id,
            step_index,
            frame_rel_path=frame_rec.rel_path,
            state_json=state_json,
            action_json=action.model_dump_json(),
            planner_raw_text=planner_raw,
            error=err,
        )

        completed += 1
        if settle > 0:
            await asyncio.sleep(settle)

    return completed
