from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    """Per-event attribution: pipeline steps or job-level agents."""

    main_events = "main_events"
    security = "security"
    logistics = "logistics"
    attendance = "attendance"
    pipeline = "pipeline"
    orchestrator = "orchestrator"


class JobCreate(BaseModel):
    """
    Enqueue a file-backed job. Provide **exactly one** of:

    - ``filename`` — basename only; resolved as ``INGEST_DIR / filename`` (best for Docker: use e.g. ``warehouse-1.mp4``).
    - ``source_path`` — absolute path **inside the same environment as the API** (in Docker: under ``/data/ingest/...``, not the host path).
    """

    source_path: str | None = Field(
        default=None,
        description="Absolute path under INGEST_DIR (container path when using Docker).",
    )
    filename: str | None = Field(
        default=None,
        description="Single file name inside INGEST_DIR (no slashes).",
    )

    @model_validator(mode="after")
    def exactly_one_source(self) -> JobCreate:
        has_path = self.source_path is not None and str(self.source_path).strip() != ""
        has_name = self.filename is not None and str(self.filename).strip() != ""
        if has_path == has_name:
            raise ValueError("Provide exactly one of 'filename' or 'source_path'")
        return self


class JobRecord(BaseModel):
    id: str
    source_type: SourceType
    source_path: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] | None = Field(
        default=None,
        description="Aggregated structured result after completion (JobSummaryPayload shape).",
    )


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


# --- Chunk structured analysis (multimodal observe + specialist text passes) ---


class ObservationItem(BaseModel):
    what: str = Field(..., min_length=1)
    where_approx: str | None = None
    when_hint: str | None = None


class ObservationsPass(BaseModel):
    scene_summary: str = ""
    observations: list[ObservationItem] = Field(default_factory=list)


class MainEventItem(BaseModel):
    title: str
    detail: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class SpecialistMainOut(BaseModel):
    main_events: list[MainEventItem] = Field(default_factory=list)


class SecurityItem(BaseModel):
    category: str
    description: str
    severity: Literal["low", "medium", "high", "info", "unknown"] = "unknown"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class LogisticsItemStructured(BaseModel):
    label: str
    description: str = ""
    action: Literal["appeared", "moved", "removed", "unknown"] = "unknown"


class SpecialistSecLogOut(BaseModel):
    security: list[SecurityItem] = Field(default_factory=list)
    logistics: list[LogisticsItemStructured] = Field(default_factory=list)


class AttendanceOut(BaseModel):
    """Counts only — no identity."""

    approx_people_visible: int | None = Field(default=None, ge=0)
    entries: int = Field(default=0, ge=0)
    exits: int = Field(default=0, ge=0)
    notes: str | None = None


class ChunkAnalysisMerged(BaseModel):
    chunk_index: int
    start_pts_ms: int
    end_pts_ms: int
    start_frame: int
    end_frame: int
    scene_summary: str = ""
    main_events: list[MainEventItem] = Field(default_factory=list)
    security: list[SecurityItem] = Field(default_factory=list)
    logistics: list[LogisticsItemStructured] = Field(default_factory=list)
    attendance: AttendanceOut = Field(default_factory=AttendanceOut)


class JobSummaryPayload(BaseModel):
    schema_version: Literal["1"] = "1"
    source_path: str
    duration_sec: float | None = None
    planned_chunk_count: int = 0
    analysed_chunk_count: int = 0
    chunk_analyses: list[ChunkAnalysisMerged] = Field(default_factory=list)


# --- Job-level agents (text-only over stored JSON) ---


class SynthesisAgentResult(BaseModel):
    """Structured output from the synthesis orchestrator (post-job summary)."""

    schema_version: Literal["1"] = "1"
    executive_summary: str = Field(
        default="",
        description="Short narrative for an operator (2–5 sentences).",
    )
    key_observations: list[str] = Field(
        default_factory=list,
        max_length=24,
        description="Bullet facts grounded in the chunk analyses.",
    )
    security_highlights: list[str] = Field(default_factory=list, max_length=16)
    logistics_highlights: list[str] = Field(default_factory=list, max_length=16)
    attendance_summary: str = Field(
        default="",
        description="One short paragraph; counts only, no identities.",
    )
    recommended_actions: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Concrete next steps for review or follow-up.",
    )


class RiskReviewAgentResult(BaseModel):
    """Security / operational risk triage from the job summary JSON (no new video)."""

    schema_version: Literal["1"] = "1"
    overall_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    requires_immediate_review: bool = False
    risk_factors: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Concrete concerns grounded in chunk-level signals.",
    )
    operator_notes: str = Field(
        default="",
        description="Short guidance for a human reviewer (2–4 sentences).",
    )
    mitigations_suggested: list[str] = Field(default_factory=list, max_length=12)


class IncidentBriefAgentResult(BaseModel):
    """Incident-style narrative from the job summary JSON (no identities, no new video)."""

    schema_version: Literal["1"] = "1"
    narrative: str = Field(
        default="",
        description="Short what-happened story for handoff (2–6 sentences).",
    )
    key_moments: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Timestamp-free bullet highlights from the summary.",
    )
    situational_factors: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Environment, flow, or context factors (not blame).",
    )
    suggested_followups: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Concrete checks or next steps.",
    )


class ComplianceBriefAgentResult(BaseModel):
    """Generic SOP / safety-alignment read across industries (warehouse, retail, plant, office)."""

    schema_version: Literal["1"] = "1"
    overall_alignment: Literal["aligned", "partial", "unclear", "concerns"] = "unclear"
    observed_practices: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Behaviours or conditions that appear consistent with common procedures.",
    )
    gaps_or_concerns: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Possible procedure gaps or hazards **only** if supported by the JSON.",
    )
    recommended_verifications: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="On-site checks an auditor or supervisor might do.",
    )
    notes: str = Field(default="", description="Short neutral summary for compliance handoff.")


class LossPreventionAgentResult(BaseModel):
    """Retail / logistics LP-style narrative without identities."""

    schema_version: Literal["1"] = "1"
    narrative: str = Field(default="", description="Sequence-oriented LP narrative (no names, no IDs).")
    behavioral_observations: list[str] = Field(default_factory=list, max_length=14)
    risk_level: Literal["low", "medium", "high", "unknown"] = "unknown"
    suggested_actions: list[str] = Field(default_factory=list, max_length=10)


class PerimeterChainAgentResult(BaseModel):
    """Ordered boundary / access storyline (sites, campuses, warehouses, utilities)."""

    schema_version: Literal["1"] = "1"
    chain_narrative: str = Field(
        default="",
        description="What happened along perimeter, entries, or access-relevant areas in order.",
    )
    key_events: list[str] = Field(default_factory=list, max_length=14)
    zones_or_segments: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Generic area labels as inferred from scene text (e.g. dock, gate, lobby).",
    )
    follow_up_checks: list[str] = Field(default_factory=list, max_length=10)


class PrivacyReviewAgentResult(BaseModel):
    """Review structured summary text for identity / sensitive-inference risks in downstream use."""

    schema_version: Literal["1"] = "1"
    overall_privacy_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    identity_inference_risks: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Ways the descriptions could enable re-identification if combined with other data.",
    )
    sensitive_descriptors: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Categories of sensitive attributes **appearing in the input** (not invented).",
    )
    safe_output_guidance: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Concrete guidance for external reporting or retention.",
    )
    summary: str = Field(default="", description="2–4 sentences for privacy / DPO handoff.")


class AgentKind(str, Enum):
    synthesis = "synthesis"
    risk_review = "risk_review"
    incident_brief = "incident_brief"
    compliance_brief = "compliance_brief"
    loss_prevention = "loss_prevention"
    perimeter_chain = "perimeter_chain"
    privacy_review = "privacy_review"


class AgentRunStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class AgentRunCreate(BaseModel):
    agent: AgentKind
    force: bool = False


class AgentOrchestrateCreate(BaseModel):
    """
    Run job-level agents **in order** (linear pipeline).

    When step *k* finishes successfully, step *k+1* is enqueued automatically.
    On any failure, the orchestration stops and is marked ``failed``.
    """

    steps: list[AgentKind] = Field(
        ...,
        min_length=1,
        max_length=24,
        description="Ordered agent kinds (e.g. full cross-industry suite).",
    )
    force: bool = Field(
        default=False,
        description="If true, each step bypasses cached orchestrator events (full LLM each time).",
    )


class AgentOrchestrationStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"


class AgentOrchestrationOut(BaseModel):
    """Multi-agent run: sequential steps with persisted status for polling."""

    id: str
    job_id: str
    status: AgentOrchestrationStatus
    steps: list[AgentKind]
    current_step: int
    force: bool = False
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentRunOut(BaseModel):
    """Persisted async agent invocation (queue row + optional result)."""

    id: str
    job_id: str
    agent: AgentKind
    status: AgentRunStatus
    force: bool = False
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    result: dict[str, Any] | None = None
    event_id: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
