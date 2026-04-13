from __future__ import annotations

import logging
from typing import Any

from overwatch.models import AgentKind
from overwatch.store import JobStore

logger = logging.getLogger(__name__)

ORCH_ID_KEY = "orchestration_id"
ORCH_STEP_KEY = "orch_step"
ORCH_STEPS_KEY = "orch_steps"


def orchestration_fields(meta: dict[str, Any]) -> dict[str, Any]:
    """Persist orchestration linkage on each ``agent_runs`` row through ``finish_agent_run``."""
    out: dict[str, Any] = {}
    if ORCH_ID_KEY in meta:
        out[ORCH_ID_KEY] = meta[ORCH_ID_KEY]
    if ORCH_STEP_KEY in meta:
        out[ORCH_STEP_KEY] = meta[ORCH_STEP_KEY]
    if ORCH_STEPS_KEY in meta:
        out[ORCH_STEPS_KEY] = meta[ORCH_STEPS_KEY]
    return out


def parse_orchestration_meta(meta: dict[str, Any]) -> tuple[str | None, int | None, list[str] | None]:
    oid = meta.get(ORCH_ID_KEY)
    if not isinstance(oid, str) or not oid:
        return None, None, None
    step = meta.get(ORCH_STEP_KEY)
    steps = meta.get(ORCH_STEPS_KEY)
    if not isinstance(step, int) or not isinstance(steps, list):
        logger.warning("Invalid orchestration meta on run (missing step or steps)")
        return oid, None, None
    step_strs = [str(s) for s in steps]
    return oid, step, step_strs


async def notify_agent_orchestration_terminal(
    store: JobStore,
    job_id: str,
    meta: dict[str, Any],
    *,
    success: bool,
    error: str | None = None,
) -> None:
    """
    After an orchestrated agent run reaches a terminal state, advance the pipeline or close the orchestration.

    Called with the **in-memory** run meta (before or after ``finish_agent_run``) so orchestration keys are present.
    """
    oid, step, steps = parse_orchestration_meta(meta)
    if oid is None or step is None or steps is None:
        return

    if not success:
        await store.fail_agent_orchestration(oid, error or "Orchestrated step failed")
        return

    orch = await store.get_agent_orchestration(oid)
    if orch is None:
        logger.warning("Orchestration %s not found after successful step", oid)
        return

    if orch.status.value != "running":
        return

    if step + 1 >= len(steps):
        await store.complete_agent_orchestration(oid)
        return

    next_i = step + 1
    try:
        next_agent = AgentKind(steps[next_i])
    except ValueError:
        await store.fail_agent_orchestration(oid, f"Invalid agent kind in orchestration: {steps[next_i]!r}")
        return

    await store.update_agent_orchestration_step(oid, current_step=next_i)
    chain_meta = {
        ORCH_ID_KEY: oid,
        ORCH_STEP_KEY: next_i,
        ORCH_STEPS_KEY: steps,
    }
    await store.create_agent_run(
        job_id,
        agent=next_agent,
        force=orch.force,
        meta=chain_meta,
    )
