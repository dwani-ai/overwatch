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

    # OpenAI-compatible root (prefix before /chat/completions), e.g. https://host/v1
    vllm_base_url: str = Field(
        default="https://vllm-video-api.dwani.ai/v1",
    )
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

    worker_poll_interval_sec: float = Field(default=1.0, ge=0.2)

    @property
    def ingest_suffixes(self) -> frozenset[str]:
        parts = [p.strip().lower() for p in self.ingest_extensions.split(",") if p.strip()]
        return frozenset(p if p.startswith(".") else f".{p}" for p in parts)
