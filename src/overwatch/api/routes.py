from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from overwatch.config import Settings
from overwatch.models import EventRecord, JobCreate, JobRecord, SourceType
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


def _ingest_root(settings: Settings) -> Path:
    return settings.ingest_dir.expanduser().resolve()


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
