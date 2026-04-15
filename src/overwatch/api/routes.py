from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from overwatch.search.models import SearchIndexStatus, SearchQuery, SearchResponse

from overwatch.agents.compliance_brief import AGENT_COMPLIANCE_BRIEF_EVENT
from overwatch.agents.incident_brief import AGENT_INCIDENT_BRIEF_EVENT
from overwatch.agents.loss_prevention import AGENT_LOSS_PREVENTION_EVENT
from overwatch.agents.perimeter_chain import AGENT_PERIMETER_CHAIN_EVENT
from overwatch.agents.privacy_review import AGENT_PRIVACY_REVIEW_EVENT
from overwatch.agents.risk_review import AGENT_RISK_REVIEW_EVENT
from overwatch.agents.synthesis import AGENT_SYNTHESIS_EVENT, run_synthesis_agent
from overwatch.industry_pipelines import pipeline_for
from overwatch.config import Settings
from overwatch.models import (
    AgentKind,
    AgentOrchestrateCreate,
    AgentOrchestrateIndustryCreate,
    AgentOrchestrationOut,
    AgentRunCreate,
    AgentRunOut,
    AgentTrack,
    EventRecord,
    JobCreate,
    JobRecord,
    JobStatus,
    SourceType,
)
from overwatch.store import JobStore

router = APIRouter(prefix="/v1")


def get_store(request: Request) -> JobStore:
    return request.app.state.store


StoreDep = Annotated[JobStore, Depends(get_store)]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/jobs", response_model=list[JobRecord])
async def list_jobs(store: StoreDep, limit: int = 50) -> list[JobRecord]:
    return await store.list_jobs(limit=min(limit, 200))


@router.get("/jobs/{job_id}", response_model=JobRecord)
async def get_job(job_id: str, store: StoreDep) -> JobRecord:
    try:
        return await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")


def _event_to_dict(e: EventRecord) -> dict:
    return {
        "id": e.id,
        "job_id": e.job_id,
        "observed_at": e.observed_at.isoformat(),
        "frame_index": e.frame_index,
        "pts_ms": e.pts_ms,
        "agent": e.agent.value,
        "event_type": e.event_type,
        "severity": e.severity,
        "payload": e.payload,
    }


@router.get("/jobs/{job_id}/events")
async def get_job_events(
    job_id: str,
    store: StoreDep,
    limit: int = 50,
    after_id: int = 0,
    legacy: bool = False,
) -> dict | list[dict]:
    """
    Paginated events: ``after_id`` is exclusive (return rows with id > after_id).
    Set ``legacy=true`` for a full non-paginated list (may be large).
    """
    try:
        await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if legacy:
        events = await store.list_events(job_id)
        return [_event_to_dict(e) for e in events]

    lim = min(max(limit, 1), 200)
    rows = await store.list_events_page(job_id, after_id=max(0, after_id), limit=lim)
    items = [_event_to_dict(e) for e in rows]
    next_after = rows[-1].id if rows and len(rows) == lim else None
    return {"items": items, "next_after_id": next_after}


@router.get("/jobs/{job_id}/summary")
async def get_job_summary(job_id: str, store: StoreDep) -> dict:
    try:
        job = await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No summary yet (job still running, failed before analysis, or pre-migration job).",
        )
    return job.summary


def _agent_orch_public_dict(o: AgentOrchestrationOut) -> dict:
    return {
        "id": o.id,
        "job_id": o.job_id,
        "status": o.status.value,
        "steps": [s.value for s in o.steps],
        "current_step": o.current_step,
        "total_steps": len(o.steps),
        "force": o.force,
        "industry_pack": o.industry_pack.value if o.industry_pack else None,
        "error": o.error,
        "created_at": o.created_at.isoformat(),
        "updated_at": o.updated_at.isoformat(),
    }


async def _get_latest_agent_event_payload(
    store: JobStore,
    job_id: str,
    *,
    event_type: str,
    not_found_detail: str,
) -> dict:
    try:
        await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    ev = await store.get_latest_event(
        job_id,
        agent=AgentTrack.orchestrator,
        event_type=event_type,
    )
    if ev is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)
    return {
        "event_id": ev.id,
        "observed_at": ev.observed_at.isoformat(),
        "result": ev.payload.get("result"),
        "error": ev.payload.get("error"),
        "attempts": ev.payload.get("attempts"),
        "truncated_input": ev.payload.get("truncated_input"),
        "model": ev.payload.get("model"),
    }


def _agent_run_public_dict(run: AgentRunOut) -> dict:
    return {
        "id": run.id,
        "job_id": run.job_id,
        "agent": run.agent.value,
        "status": run.status.value,
        "force": run.force,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "error": run.error,
        "result": run.result,
        "event_id": run.event_id,
        "meta": run.meta,
    }


def _require_job_for_agents(job: JobRecord) -> None:
    if job.status != JobStatus.completed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job must be completed before running agents",
        )
    if not job.summary:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job has no summary — enable VLLM and ensure chunk analysis produced a summary",
        )


def _require_vllm_configured(settings: Settings) -> None:
    if not settings.vllm_base_url.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VLLM_BASE_URL is not configured",
        )


@router.post("/jobs/{job_id}/agent-runs")
async def enqueue_agent_run(
    job_id: str,
    request: Request,
    store: StoreDep,
    body: AgentRunCreate,
) -> JSONResponse:
    """
    Queue an agent run (**async**). Returns **202** with ``run_id``; poll ``GET /v1/agent-runs/{run_id}``.
    """
    settings: Settings = request.app.state.settings
    try:
        job = await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _require_job_for_agents(job)
    _require_vllm_configured(settings)

    run = await store.create_agent_run(job_id, agent=body.agent, force=body.force)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "run_id": run.id,
            "job_id": run.job_id,
            "agent": run.agent.value,
            "status": run.status.value,
            "created_at": run.created_at.isoformat(),
            "poll_url": f"/v1/agent-runs/{run.id}",
        },
    )


@router.post("/jobs/{job_id}/agent-runs/orchestrate")
async def orchestrate_agent_runs(
    job_id: str,
    request: Request,
    store: StoreDep,
    body: AgentOrchestrateCreate,
) -> JSONResponse:
    """
    Start a **sequential** multi-agent run: when each step finishes successfully, the next is enqueued.
    Poll ``GET /v1/agent-orchestrations/{id}`` for pipeline status and ``GET /v1/agent-runs/{run_id}`` for the active step.
    """
    settings: Settings = request.app.state.settings
    try:
        job = await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _require_job_for_agents(job)
    _require_vllm_configured(settings)

    if await store.job_has_active_agent_orchestration(job_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An orchestration is already running for this job. Wait for it to finish or fail.",
        )

    orch, head = await store.start_agent_orchestration(job_id, body.steps, force=body.force)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "orchestration_id": orch.id,
            "job_id": orch.job_id,
            "steps": [s.value for s in orch.steps],
            "status": orch.status.value,
            "current_step": orch.current_step,
            "total_steps": len(orch.steps),
            "force": orch.force,
            "industry_pack": None,
            "head_run_id": head.id,
            "poll_url": f"/v1/agent-orchestrations/{orch.id}",
            "head_run_poll_url": f"/v1/agent-runs/{head.id}",
            "detail": "Poll orchestration until status is completed or failed; poll head_run_id while a step runs.",
        },
    )


@router.post("/jobs/{job_id}/agent-runs/orchestrate/industry")
async def orchestrate_industry_agent_runs(
    job_id: str,
    request: Request,
    store: StoreDep,
    body: AgentOrchestrateIndustryCreate,
) -> JSONResponse:
    """
    Start a **named industry pipeline**: a curated agent order for the selected vertical (static graph).

    Prefer this over ad-hoc ``steps`` when you want reproducible, auditable multi-agent runs per industry.
    """
    settings: Settings = request.app.state.settings
    try:
        job = await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _require_job_for_agents(job)
    _require_vllm_configured(settings)

    if await store.job_has_active_agent_orchestration(job_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An orchestration is already running for this job. Wait for it to finish or fail.",
        )

    steps = pipeline_for(body.industry)
    orch, head = await store.start_agent_orchestration(
        job_id,
        steps,
        force=body.force,
        industry_pack=body.industry,
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "orchestration_id": orch.id,
            "job_id": orch.job_id,
            "industry_pack": body.industry.value,
            "steps": [s.value for s in orch.steps],
            "status": orch.status.value,
            "current_step": orch.current_step,
            "total_steps": len(orch.steps),
            "force": orch.force,
            "head_run_id": head.id,
            "poll_url": f"/v1/agent-orchestrations/{orch.id}",
            "head_run_poll_url": f"/v1/agent-runs/{head.id}",
            "detail": "Named vertical pipeline; poll orchestration until completed or failed.",
        },
    )


@router.get("/agent-orchestrations/{orch_id}")
async def get_agent_orchestration(orch_id: str, store: StoreDep) -> dict:
    orch = await store.get_agent_orchestration(orch_id)
    if orch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Orchestration not found")
    return _agent_orch_public_dict(orch)


@router.get("/jobs/{job_id}/agent-orchestrations")
async def list_job_agent_orchestrations(job_id: str, store: StoreDep, limit: int = 20) -> dict:
    try:
        await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    rows = await store.list_agent_orchestrations_for_job(job_id, limit=min(max(limit, 1), 50))
    return {"items": [_agent_orch_public_dict(o) for o in rows]}


@router.get("/agent-runs/{run_id}")
async def get_agent_run(run_id: str, store: StoreDep) -> dict:
    run = await store.get_agent_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return _agent_run_public_dict(run)


@router.get("/jobs/{job_id}/agent-runs")
async def list_job_agent_runs(job_id: str, store: StoreDep, limit: int = 30) -> dict:
    try:
        await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    runs = await store.list_agent_runs_for_job(job_id, limit=min(max(limit, 1), 100))
    return {"items": [_agent_run_public_dict(r) for r in runs]}


@router.get("/jobs/{job_id}/agents/synthesis")
async def get_job_synthesis(job_id: str, store: StoreDep) -> dict:
    """Return the latest stored synthesis agent output for this job, if any."""
    try:
        await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    ev = await store.get_latest_event(
        job_id,
        agent=AgentTrack.orchestrator,
        event_type=AGENT_SYNTHESIS_EVENT,
    )
    if ev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No synthesis run yet. POST /v1/jobs/{id}/agent-runs with agent=synthesis or POST …/agents/synthesis?blocking=true.",
        )
    return {
        "event_id": ev.id,
        "observed_at": ev.observed_at.isoformat(),
        "result": ev.payload.get("result"),
        "error": ev.payload.get("error"),
        "attempts": ev.payload.get("attempts"),
        "truncated_input": ev.payload.get("truncated_input"),
        "model": ev.payload.get("model"),
    }


@router.get("/jobs/{job_id}/agents/risk-review")
async def get_job_risk_review(job_id: str, store: StoreDep) -> dict:
    """Return the latest stored **risk review** agent output for this job, if any."""
    try:
        await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    ev = await store.get_latest_event(
        job_id,
        agent=AgentTrack.orchestrator,
        event_type=AGENT_RISK_REVIEW_EVENT,
    )
    if ev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No risk review run yet. POST /v1/jobs/{id}/agent-runs with agent=risk_review.",
        )
    return {
        "event_id": ev.id,
        "observed_at": ev.observed_at.isoformat(),
        "result": ev.payload.get("result"),
        "error": ev.payload.get("error"),
        "attempts": ev.payload.get("attempts"),
        "truncated_input": ev.payload.get("truncated_input"),
        "model": ev.payload.get("model"),
    }


@router.get("/jobs/{job_id}/agents/incident-brief")
async def get_job_incident_brief(job_id: str, store: StoreDep) -> dict:
    """Return the latest stored **incident brief** agent output for this job, if any."""
    return await _get_latest_agent_event_payload(
        store,
        job_id,
        event_type=AGENT_INCIDENT_BRIEF_EVENT,
        not_found_detail="No incident brief run yet. POST /v1/jobs/{id}/agent-runs with agent=incident_brief.",
    )


@router.get("/jobs/{job_id}/agents/compliance-brief")
async def get_job_compliance_brief(job_id: str, store: StoreDep) -> dict:
    return await _get_latest_agent_event_payload(
        store,
        job_id,
        event_type=AGENT_COMPLIANCE_BRIEF_EVENT,
        not_found_detail="No compliance brief run yet. POST /v1/jobs/{id}/agent-runs with agent=compliance_brief.",
    )


@router.get("/jobs/{job_id}/agents/loss-prevention")
async def get_job_loss_prevention(job_id: str, store: StoreDep) -> dict:
    return await _get_latest_agent_event_payload(
        store,
        job_id,
        event_type=AGENT_LOSS_PREVENTION_EVENT,
        not_found_detail="No loss prevention run yet. POST /v1/jobs/{id}/agent-runs with agent=loss_prevention.",
    )


@router.get("/jobs/{job_id}/agents/perimeter-chain")
async def get_job_perimeter_chain(job_id: str, store: StoreDep) -> dict:
    return await _get_latest_agent_event_payload(
        store,
        job_id,
        event_type=AGENT_PERIMETER_CHAIN_EVENT,
        not_found_detail="No perimeter chain run yet. POST /v1/jobs/{id}/agent-runs with agent=perimeter_chain.",
    )


@router.get("/jobs/{job_id}/agents/privacy-review")
async def get_job_privacy_review(job_id: str, store: StoreDep) -> dict:
    return await _get_latest_agent_event_payload(
        store,
        job_id,
        event_type=AGENT_PRIVACY_REVIEW_EVENT,
        not_found_detail="No privacy review run yet. POST /v1/jobs/{id}/agent-runs with agent=privacy_review.",
    )


@router.post("/jobs/{job_id}/agents/synthesis")
async def post_job_synthesis(
    job_id: str,
    request: Request,
    store: StoreDep,
    force: bool = False,
    blocking: bool = False,
) -> Any:
    """
    Run the **synthesis** orchestrator.

    - Default ``blocking=false``: enqueue an async run (**202** + ``run_id``); poll ``GET /v1/agent-runs/{run_id}``.
    - ``blocking=true``: wait for the LLM inline (legacy; same as original behaviour).
    """
    settings: Settings = request.app.state.settings
    try:
        job = await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _require_job_for_agents(job)
    _require_vllm_configured(settings)

    if not blocking:
        run = await store.create_agent_run(job_id, agent=AgentKind.synthesis, force=force)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "run_id": run.id,
                "job_id": run.job_id,
                "agent": run.agent.value,
                "status": run.status.value,
                "created_at": run.created_at.isoformat(),
                "poll_url": f"/v1/agent-runs/{run.id}",
                "detail": "Poll GET /v1/agent-runs/{run_id} until status is completed or failed.",
            },
        )

    if not force:
        prev = await store.get_latest_event(
            job_id,
            agent=AgentTrack.orchestrator,
            event_type=AGENT_SYNTHESIS_EVENT,
        )
        if (
            prev is not None
            and prev.payload.get("result") is not None
            and not prev.payload.get("error")
        ):
            return {
                "cached": True,
                "event_id": prev.id,
                "observed_at": prev.observed_at.isoformat(),
                "result": prev.payload["result"],
            }

    result, meta = await run_synthesis_agent(settings, job.summary)
    payload: dict = {
        "agent_id": "synthesis",
        "result": result.model_dump() if result is not None else None,
        "error": meta.get("error"),
        "attempts": meta.get("attempts"),
        "truncated_input": meta.get("truncated_input", False),
        "model": meta.get("model"),
    }
    severity = "error" if result is None else None
    event_id = await store.append_event(
        job_id,
        agent=AgentTrack.orchestrator,
        event_type=AGENT_SYNTHESIS_EVENT,
        payload=payload,
        severity=severity,
    )
    latest = await store.get_latest_event(
        job_id,
        agent=AgentTrack.orchestrator,
        event_type=AGENT_SYNTHESIS_EVENT,
    )
    observed_at = latest.observed_at.isoformat() if latest else ""
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(meta.get("error") or "Synthesis agent failed"),
        )
    return {
        "cached": False,
        "event_id": event_id,
        "observed_at": observed_at,
        "result": result.model_dump(),
    }


def _ingest_root(settings: Settings) -> Path:
    root = settings.ingest_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_stored_filename(original: str | None) -> str:
    p = Path(original or "video.mp4")
    name = p.name
    if not name or ".." in name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name",
        )
    suf = p.suffix.lower() or ".mp4"
    stem = re.sub(r"[^a-zA-Z0-9._-]", "_", p.stem).strip("._") or "video"
    return f"{stem[:160]}{suf}"


def _reject_not_under_ingest(path: Path, ingest_root: Path) -> None:
    try:
        path.relative_to(ingest_root)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Path must be under INGEST_DIR. "
                "In Docker, INGEST_DIR is usually /data/ingest — use e.g. "
                '"/data/ingest/video.mp4" or POST {"filename":"video.mp4"} instead of a host path '
                "like /home/..."
            ),
        ) from None


@router.post("/jobs/upload", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
async def upload_job(
    request: Request,
    store: StoreDep,
    file: UploadFile = File(...),
) -> JobRecord:
    """Save an uploaded video under ``INGEST_DIR`` and enqueue a processing job."""
    settings = request.app.state.settings
    ingest_root = _ingest_root(settings)
    suffix = Path(file.filename or "").suffix.lower() or ".mp4"
    if suffix not in settings.ingest_suffixes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type {suffix!r}; allowed: {sorted(settings.ingest_suffixes)}",
        )
    safe = _safe_stored_filename(file.filename)
    dest = (ingest_root / f"{uuid.uuid4().hex}_{safe}").resolve()
    if not dest.is_relative_to(ingest_root):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")

    max_b = settings.max_upload_bytes
    cl_hdr = request.headers.get("content-length")
    if cl_hdr and cl_hdr.isdigit() and int(cl_hdr) > max_b:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload too large (max {max_b} bytes)",
        )

    chunk_size = 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                block = await file.read(chunk_size)
                if not block:
                    break
                written += len(block)
                if written > max_b:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload too large (max {max_b} bytes)",
                    )
                out.write(block)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    st = dest.stat()
    mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
    fp = f"{st.st_size}:{mtime_ns}"
    return await store.create_job(
        source_type=SourceType.file,
        source_path=str(dest),
        meta={"fingerprint": fp, "ingest": "upload"},
    )


@router.post("/jobs", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate, request: Request, store: StoreDep) -> JobRecord:
    settings = request.app.state.settings
    ingest_root = _ingest_root(settings)

    if body.filename is not None and str(body.filename).strip():
        fn = str(body.filename).strip()
        if "/" in fn or "\\" in fn or fn in (".", "..") or ".." in fn:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="filename must be a single file name (no path separators)",
            )
        path = (ingest_root / fn).resolve()
        if not path.is_relative_to(ingest_root):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid filename for INGEST_DIR",
            )
    else:
        path = Path(str(body.source_path).strip()).expanduser().resolve()
        _reject_not_under_ingest(path, ingest_root)

    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File does not exist")

    st = path.stat()
    mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
    fp = f"{st.st_size}:{mtime_ns}"
    return await store.create_job(
        source_type=SourceType.file,
        source_path=str(path),
        meta={"fingerprint": fp, "ingest": "api"},
    )


# ---------------------------------------------------------------------------
# Search / RAG
# ---------------------------------------------------------------------------


def _get_retriever(request: Request):
    retriever = getattr(request.app.state, "search_retriever", None)
    if retriever is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Search is not available. "
                "Ensure SEARCH_ENABLED=true and that chromadb, sentence-transformers, "
                "and rank-bm25 are installed."
            ),
        )
    return retriever


def _get_indexer(request: Request):
    indexer = getattr(request.app.state, "search_indexer", None)
    if indexer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search indexer is not available.",
        )
    return indexer


@router.post("/search", response_model=SearchResponse)
async def search_events(body: SearchQuery, request: Request) -> SearchResponse:
    """
    Hybrid RAG search over all indexed video analysis events.

    Combines ChromaDB vector search with BM25 keyword search (RRF fusion).
    Set ``synthesize_answer=true`` to generate an LLM answer from the top results
    (requires ``VLLM_BASE_URL`` and ``SEARCH_ANSWER_ENABLED=true``).
    """
    retriever = _get_retriever(request)
    settings = request.app.state.settings

    # Guard against answer synthesis when disabled in config
    if body.synthesize_answer and not settings.search_answer_enabled:
        body = body.model_copy(update={"synthesize_answer": False})

    return await retriever.search(body)


@router.get("/search/index-status", response_model=SearchIndexStatus)
async def search_index_status(request: Request) -> SearchIndexStatus:
    """Return the current state of the search index."""
    indexer = getattr(request.app.state, "search_indexer", None)
    if indexer is None:
        return SearchIndexStatus(
            enabled=False,
            total_documents=0,
            collection_name="overwatch_events",
            embedding_model="",
        )
    import asyncio

    raw = await asyncio.to_thread(indexer.get_status)
    return SearchIndexStatus(**raw)
