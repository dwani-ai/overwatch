from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from overwatch.models import JobCreate, JobRecord, SourceType
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


@router.get("/jobs/{job_id}/events")
async def get_job_events(job_id: str, store: StoreDep) -> list[dict]:
    try:
        await store.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    events = await store.list_events(job_id)
    return [
        {
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
        for e in events
    ]


@router.post("/jobs", response_model=JobRecord, status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate, request: Request, store: StoreDep) -> JobRecord:
    settings = request.app.state.settings
    path = Path(body.source_path).resolve()
    try:
        path.relative_to(settings.ingest_dir.resolve())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_path must be under INGEST_DIR",
        )
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
