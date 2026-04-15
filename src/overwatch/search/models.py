from __future__ import annotations

from pydantic import BaseModel, Field


class SearchSource(BaseModel):
    """Citation pointing to the exact origin of a search result."""

    job_id: str
    source_path: str
    video_filename: str
    chunk_index: int | None = None
    start_pts_ms: int | None = None
    end_pts_ms: int | None = None
    agent_type: str
    content_type: str
    severity: str | None = None


class SearchResult(BaseModel):
    text: str
    score: float
    source: SearchSource


class SearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)
    job_ids: list[str] | None = Field(
        default=None,
        description="Restrict search to these job IDs. Omit for cross-job search.",
    )
    agent_types: list[str] | None = Field(
        default=None,
        description="Filter by agent type (e.g. 'chunk_analysis', 'risk_review').",
    )
    severity: str | None = Field(
        default=None,
        description="Filter by severity label (e.g. 'high', 'medium', 'low').",
    )
    synthesize_answer: bool = Field(
        default=False,
        description="Generate a short LLM answer from the top results (requires VLLM_BASE_URL).",
    )


class SearchResponse(BaseModel):
    query: str
    answer: str | None = None
    results: list[SearchResult]
    total_found: int


class SearchIndexStatus(BaseModel):
    enabled: bool
    total_documents: int
    collection_name: str
    embedding_model: str
