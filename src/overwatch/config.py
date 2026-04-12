from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("./data/overwatch"))
    ingest_dir: Path = Field(default=Path("./data/ingest"))

    ingest_poll_interval_sec: float = Field(default=5.0, ge=0.5)
    ingest_stable_sec: float = Field(default=2.0, ge=0.0)
    ingest_extensions: str = Field(default=".mp4,.mkv,.mov,.avi,.webm,.m4v")

    # OpenAI-compatible root (prefix before /chat/completions). Set via env; empty disables vLLM.
    vllm_base_url: str = Field(default="")
    vllm_model: str = Field(default="gemma4")
    vllm_api_key: str | None = Field(default=None)
    vllm_chat_timeout_sec: float = Field(default=120.0, ge=5.0)
    # Per-chunk video → vLLM (multimodal chat/completions)
    vllm_multimodal_enabled: bool = Field(default=True)
    vllm_max_chunks_per_job: int = Field(default=4, ge=0, le=64)
    vllm_chunk_timeout_sec: float = Field(default=600.0, ge=30.0)
    vllm_chunk_max_tokens: int = Field(default=1024, ge=64, le=8192)
    vllm_segment_max_bytes: int = Field(default=18_000_000, ge=1_000_000)
    vllm_video_scale_width: int = Field(default=480, ge=160, le=1280)
    vllm_video_crf: int = Field(default=30, ge=18, le=40)
    vllm_segment_include_audio: bool = Field(default=True)
    vllm_json_retry_max: int = Field(default=2, ge=1, le=6)
    vllm_specialist_max_tokens: int = Field(default=800, ge=64, le=4096)
    # Text-only job agents (e.g. synthesis over JobSummary JSON)
    vllm_agent_max_tokens: int = Field(default=2048, ge=256, le=8192)
    vllm_agent_timeout_sec: float = Field(default=120.0, ge=10.0)

    worker_poll_interval_sec: float = Field(default=1.0, ge=0.2)

    # Comma-separated origins for browser UI (http://localhost omits :80; include :3000 if you remap the UI port)
    cors_origins: str = Field(
        default=(
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost,http://127.0.0.1,"
            "http://localhost:3000,http://127.0.0.1:3000"
        )
    )

    @property
    def ingest_suffixes(self) -> frozenset[str]:
        parts = [p.strip().lower() for p in self.ingest_extensions.split(",") if p.strip()]
        return frozenset(p if p.startswith(".") else f".{p}" for p in parts)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
