from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from overwatch.search.indexer import SearchIndexer

from overwatch.agents.compliance_brief import AGENT_COMPLIANCE_BRIEF_EVENT, run_compliance_brief_agent
from overwatch.agents.incident_brief import AGENT_INCIDENT_BRIEF_EVENT, run_incident_brief_agent
from overwatch.agents.loss_prevention import AGENT_LOSS_PREVENTION_EVENT, run_loss_prevention_agent
from overwatch.agents.orchestration import (
    notify_agent_orchestration_terminal,
    orchestration_fields,
)
from overwatch.agents.perimeter_chain import AGENT_PERIMETER_CHAIN_EVENT, run_perimeter_chain_agent
from overwatch.agents.privacy_review import AGENT_PRIVACY_REVIEW_EVENT, run_privacy_review_agent
from overwatch.agents.risk_review import AGENT_RISK_REVIEW_EVENT, run_risk_review_agent
from overwatch.agents.synthesis import AGENT_SYNTHESIS_EVENT, run_synthesis_agent
from overwatch.config import Settings
from overwatch.models import AgentKind, AgentRunOut, AgentRunStatus, AgentTrack, JobStatus
from overwatch.store import JobStore

logger = logging.getLogger(__name__)

_STALE_SWEEP_INTERVAL_SEC = 30.0

_EVENT_TYPE: dict[AgentKind, str] = {
    AgentKind.synthesis: AGENT_SYNTHESIS_EVENT,
    AgentKind.risk_review: AGENT_RISK_REVIEW_EVENT,
    AgentKind.incident_brief: AGENT_INCIDENT_BRIEF_EVENT,
    AgentKind.compliance_brief: AGENT_COMPLIANCE_BRIEF_EVENT,
    AgentKind.loss_prevention: AGENT_LOSS_PREVENTION_EVENT,
    AgentKind.perimeter_chain: AGENT_PERIMETER_CHAIN_EVENT,
    AgentKind.privacy_review: AGENT_PRIVACY_REVIEW_EVENT,
}

_AGENT_PAYLOAD_ID: dict[AgentKind, str] = {
    AgentKind.synthesis: "synthesis",
    AgentKind.risk_review: "risk_review",
    AgentKind.incident_brief: "incident_brief",
    AgentKind.compliance_brief: "compliance_brief",
    AgentKind.loss_prevention: "loss_prevention",
    AgentKind.perimeter_chain: "perimeter_chain",
    AgentKind.privacy_review: "privacy_review",
}

_AgentRunner = Callable[
    [Settings, dict[str, Any]],
    Awaitable[tuple[Any, dict[str, Any]]],
]

_AGENT_RUNNERS: dict[AgentKind, _AgentRunner] = {
    AgentKind.synthesis: run_synthesis_agent,
    AgentKind.risk_review: run_risk_review_agent,
    AgentKind.incident_brief: run_incident_brief_agent,
    AgentKind.compliance_brief: run_compliance_brief_agent,
    AgentKind.loss_prevention: run_loss_prevention_agent,
    AgentKind.perimeter_chain: run_perimeter_chain_agent,
    AgentKind.privacy_review: run_privacy_review_agent,
}


def _clean_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in meta.items() if v is not None}


async def process_agent_run(
    store: JobStore,
    settings: Settings,
    run: AgentRunOut,
    indexer: SearchIndexer | None = None,
) -> None:
    """Execute one claimed agent run (LLM + event + ``agent_runs`` row update)."""
    run_id = run.id
    orch_base = orchestration_fields(run.meta)

    try:
        job = await store.get_job(run.job_id)
    except KeyError:
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error="Job not found",
            meta=orch_base,
        )
        await notify_agent_orchestration_terminal(
            store, run.job_id, run.meta, success=False, error="Job not found"
        )
        return

    if job.status != JobStatus.completed or not job.summary:
        msg = "Job must be completed with a summary before running agents"
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error=msg,
            meta=orch_base,
        )
        await notify_agent_orchestration_terminal(store, run.job_id, run.meta, success=False, error=msg)
        return

    if not settings.vllm_base_url.strip():
        msg = "VLLM_BASE_URL is not configured"
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error=msg,
            meta=orch_base,
        )
        await notify_agent_orchestration_terminal(store, run.job_id, run.meta, success=False, error=msg)
        return

    ev_type = _EVENT_TYPE.get(run.agent)
    runner_fn = _AGENT_RUNNERS.get(run.agent)
    if ev_type is None or runner_fn is None:
        msg = f"Unsupported agent kind: {run.agent.value}"
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error=msg,
            meta=orch_base,
        )
        await notify_agent_orchestration_terminal(store, run.job_id, run.meta, success=False, error=msg)
        return

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
                **orch_base,
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
            await notify_agent_orchestration_terminal(store, run.job_id, run.meta, success=True)
            return

    result_model, meta = await runner_fn(settings, job.summary)

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
        err = str(meta.get("error") or "Agent failed")
        fin_meta = {
            **orch_base,
            **_clean_meta({k: v for k, v in meta.items() if k != "error"}),
        }
        await store.finish_agent_run(
            run_id,
            status=AgentRunStatus.failed,
            error=err,
            event_id=event_id,
            meta=fin_meta,
        )
        await notify_agent_orchestration_terminal(store, run.job_id, run.meta, success=False, error=err)
        return

    fin_meta = {
        **orch_base,
        **_clean_meta({k: v for k, v in meta.items() if k != "error"}),
    }
    await store.finish_agent_run(
        run_id,
        status=AgentRunStatus.completed,
        result=result_model.model_dump(),
        event_id=event_id,
        meta=fin_meta,
    )
    if indexer is not None:
        try:
            agent_kind = _AGENT_PAYLOAD_ID.get(run.agent, run.agent.value)
            await asyncio.to_thread(
                indexer.index_agent_result,
                run.job_id,
                job.source_path,
                agent_kind,
                result_model.model_dump(),
            )
        except Exception:
            logger.warning(
                "Search index update failed for agent %s job %s",
                run.agent.value,
                run.job_id,
                exc_info=True,
            )
    await notify_agent_orchestration_terminal(store, run.job_id, run.meta, success=True)


async def agent_worker_loop(
    store: JobStore,
    settings: Settings,
    stop: asyncio.Event,
    indexer: SearchIndexer | None = None,
) -> None:
    """Poll for pending ``agent_runs`` rows and process them sequentially."""
    interval = settings.agent_worker_poll_interval_sec
    last_stale_sweep = 0.0
    while not stop.is_set():
        try:
            run = await store.claim_next_agent_run()
            if run is None:
                now_m = time.monotonic()
                if now_m - last_stale_sweep >= _STALE_SWEEP_INTERVAL_SEC:
                    last_stale_sweep = now_m
                    n = await store.fail_stale_agent_runs(older_than_sec=settings.agent_run_stale_sec)
                    if n:
                        logger.info("Marked %s stale agent run(s) as failed", n)
                await asyncio.sleep(interval)
                continue
            try:
                await process_agent_run(store, settings, run, indexer=indexer)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Agent run %s failed with unexpected error", run.id)
                try:
                    msg = "Internal error while running agent"
                    await store.finish_agent_run(
                        run.id,
                        status=AgentRunStatus.failed,
                        error=msg,
                        meta=orchestration_fields(run.meta),
                    )
                    await notify_agent_orchestration_terminal(
                        store, run.job_id, run.meta, success=False, error=msg
                    )
                except Exception:
                    logger.exception("Could not mark agent run %s failed", run.id)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Agent worker loop error")
            await asyncio.sleep(interval)
