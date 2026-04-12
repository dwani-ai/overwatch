from __future__ import annotations

import asyncio
import logging
from typing import Any

from overwatch.agents.risk_review import AGENT_RISK_REVIEW_EVENT, run_risk_review_agent
from overwatch.agents.synthesis import AGENT_SYNTHESIS_EVENT, run_synthesis_agent
from overwatch.config import Settings
from overwatch.models import AgentKind, AgentRunOut, AgentRunStatus, AgentTrack, JobStatus
from overwatch.store import JobStore

logger = logging.getLogger(__name__)

_EVENT_TYPE: dict[AgentKind, str] = {
    AgentKind.synthesis: AGENT_SYNTHESIS_EVENT,
    AgentKind.risk_review: AGENT_RISK_REVIEW_EVENT,
}

_AGENT_PAYLOAD_ID: dict[AgentKind, str] = {
    AgentKind.synthesis: "synthesis",
    AgentKind.risk_review: "risk_review",
}


def _clean_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in meta.items() if v is not None}


async def process_agent_run(store: JobStore, settings: Settings, run: AgentRunOut) -> None:
    """Execute one claimed agent run (LLM + event + ``agent_runs`` row update)."""
    run_id = run.id
    try:
        job = await store.get_job(run.job_id)
    except KeyError:
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error="Job not found",
        )
        return

    if job.status != JobStatus.completed or not job.summary:
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error="Job must be completed with a summary before running agents",
        )
        return

    if not settings.vllm_base_url.strip():
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error="VLLM_BASE_URL is not configured",
        )
        return

    ev_type = _EVENT_TYPE[run.agent]

    if not run.force:
        prev = await store.get_latest_event(
            run.job_id,
            agent=AgentTrack.orchestrator,
            event_type=ev_type,
        )
        if (
            prev is not None
            and prev.payload.get("result") is not None
            and not prev.payload.get("error")
        ):
            res = prev.payload["result"]
            meta = {
                "cached": True,
                "attempts": 0,
                "model": settings.vllm_model,
                "from_event_id": prev.id,
            }
            await store.finish_agent_run(
                run_id,
                status=AgentRunStatus.completed,
                result=res if isinstance(res, dict) else None,
                event_id=prev.id,
                meta=meta,
            )
            return

    if run.agent == AgentKind.synthesis:
        result_model, meta = await run_synthesis_agent(settings, job.summary)
    else:
        result_model, meta = await run_risk_review_agent(settings, job.summary)

    payload: dict[str, Any] = {
        "agent_id": _AGENT_PAYLOAD_ID[run.agent],
        "result": result_model.model_dump() if result_model is not None else None,
        "error": meta.get("error"),
        "attempts": meta.get("attempts"),
        "truncated_input": meta.get("truncated_input", False),
        "model": meta.get("model"),
    }
    severity = "error" if result_model is None else None
    event_id = await store.append_event(
        run.job_id,
        agent=AgentTrack.orchestrator,
        event_type=ev_type,
        payload=payload,
        severity=severity,
    )

    if result_model is None:
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error=str(meta.get("error") or "Agent failed"),
            event_id=event_id,
            meta=_clean_meta({k: v for k, v in meta.items() if k != "error"}),
        )
        return

    await store.finish_agent_run(
        run_id,
        status=AgentRunStatus.completed,
        result=result_model.model_dump(),
        event_id=event_id,
        meta=_clean_meta({k: v for k, v in meta.items() if k != "error"}),
    )


async def agent_worker_loop(store: JobStore, settings: Settings, stop: asyncio.Event) -> None:
    """Poll for pending ``agent_runs`` rows and process them sequentially."""
    interval = settings.agent_worker_poll_interval_sec
    while not stop.is_set():
        try:
            run = await store.claim_next_agent_run()
            if run is None:
                await asyncio.sleep(interval)
                continue
            try:
                await process_agent_run(store, settings, run)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Agent run %s failed with unexpected error", run.id)
                try:
                    await store.finish_agent_run(
                        run.id,
                        status=AgentRunStatus.failed,
                        error="Internal error while running agent",
                    )
                except Exception:
                    logger.exception("Could not mark agent run %s failed", run.id)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Agent worker loop error")
            await asyncio.sleep(interval)
