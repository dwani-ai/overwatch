from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from overwatch.config import Settings
from overwatch.models import (
    AgentTrack,
    JobStatus,
    PipelineChunkPlanPayload,
    PipelineProbePayload,
    SourceType,
)
from overwatch.store import JobStore
from overwatch.video import ffprobe, plan_chunks
from overwatch.vllm_client import (
    chat_completion,
    extract_assistant_text,
    fetch_models,
)

logger = logging.getLogger(__name__)


async def process_one_job(store: JobStore, settings: Settings) -> bool:
    job = await store.next_pending_job()
    if job is None:
        return False

    await store.update_job_status(job.id, JobStatus.processing)
    fp = (job.meta or {}).get("fingerprint")

    try:
        if job.source_type != SourceType.file:
            raise NotImplementedError(f"Source type {job.source_type} not implemented yet")

        path = Path(job.source_path)
        if not path.is_file():
            raise FileNotFoundError(str(path))

        probe = await ffprobe(path)
        await store.append_event(
            job.id,
            agent=AgentTrack.pipeline,
            event_type="probe",
            payload=PipelineProbePayload(
                duration_sec=probe.duration_sec,
                avg_frame_rate=probe.avg_frame_rate,
                width=probe.width,
                height=probe.height,
                codec=probe.codec,
            ).model_dump(),
            frame_index=0,
            pts_ms=0,
        )

        chunks = plan_chunks(probe, target_fps=1.0, max_chunk_sec=60.0)
        await store.append_event(
            job.id,
            agent=AgentTrack.pipeline,
            event_type="chunk_plan",
            payload=PipelineChunkPlanPayload(target_fps=1.0, chunks=chunks).model_dump(),
        )

        base = settings.vllm_base_url.strip()
        if base:
            models_res = await fetch_models(base, api_key=settings.vllm_api_key)
            await store.append_event(
                job.id,
                agent=AgentTrack.pipeline,
                event_type="vllm_models",
                payload=models_res.to_event_payload(response_key="models"),
            )

            summary = (
                f"Overwatch phase-1 job summary (text only; multimodal/SAM later).\n"
                f"source_path={job.source_path}\n"
                f"duration_sec={probe.duration_sec}\n"
                f"avg_frame_rate={probe.avg_frame_rate}\n"
                f"resolution={probe.width}x{probe.height}\n"
                f"codec={probe.codec}\n"
                f"planned_chunks={len(chunks)}\n\n"
                "Reply briefly: (1) confirm you received this, "
                "(2) one sentence on how you would analyse this video once frames or clips are attached."
            )
            chat_res = await chat_completion(
                base,
                model=settings.vllm_model,
                messages=[{"role": "user", "content": summary}],
                api_key=settings.vllm_api_key,
                timeout_sec=settings.vllm_chat_timeout_sec,
            )
            text = extract_assistant_text(chat_res.data)
            choices = chat_res.data.get("choices", []) if chat_res.data else []
            await store.append_event(
                job.id,
                agent=AgentTrack.pipeline,
                event_type="vllm_chat",
                payload=chat_res.to_event_payload(
                    model=settings.vllm_model,
                    assistant_preview=(text[:2000] + "…") if text and len(text) > 2000 else text,
                    raw_choice_count=len(choices),
                ),
            )

        await store.update_job_status(job.id, JobStatus.completed)
        if fp:
            await store.record_processed_file(job.source_path, str(fp), job.id)
        return True

    except Exception as e:
        logger.exception("Job %s failed", job.id)
        await store.append_event(
            job.id,
            agent=AgentTrack.pipeline,
            event_type="error",
            payload={"message": str(e)},
            severity="error",
        )
        await store.update_job_status(job.id, JobStatus.failed, error=str(e))
        return True


async def worker_loop(store: JobStore, settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        worked = await process_one_job(store, settings)
        if not worked:
            await asyncio.sleep(settings.worker_poll_interval_sec)
