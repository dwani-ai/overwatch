from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceType(str, Enum):
    file = "file"
    rtsp_segment = "rtsp_segment"


class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class AgentTrack(str, Enum):
    main_events = "main_events"
    security = "security"
    logistics = "logistics"
    attendance = "attendance"
    pipeline = "pipeline"


class JobCreate(BaseModel):
    """Enqueue a file-backed job (path must exist and be readable)."""

    source_path: str = Field(..., description="Absolute path to a video file inside the container/host mount.")


class JobRecord(BaseModel):
    id: str
    source_type: SourceType
    source_path: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class EventRecord(BaseModel):
    id: int
    job_id: str
    observed_at: datetime
    frame_index: int | None = None
    pts_ms: int | None = None
    agent: AgentTrack
    event_type: str
    severity: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# --- Structured payloads (contracts v0) ---


class PipelineProbePayload(BaseModel):
    duration_sec: float | None = None
    avg_frame_rate: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None


class ChunkPlanItem(BaseModel):
    chunk_index: int
    start_frame: int
    end_frame: int
    start_pts_ms: int
    end_pts_ms: int


class PipelineChunkPlanPayload(BaseModel):
    target_fps: float
    chunks: list[ChunkPlanItem]


class MainEventPayload(BaseModel):
    summary: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class SecurityIssuePayload(BaseModel):
    category: str
    description: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class LogisticsItemPayload(BaseModel):
    label: str
    action: Literal["appeared", "moved", "removed"] | None = None


class AttendanceCountPayload(BaseModel):
    """Counts only — no identity fields."""

    zone_id: str | None = None
    entries: int = Field(ge=0)
    exits: int = Field(ge=0)
    window_start_ms: int | None = None
    window_end_ms: int | None = None
