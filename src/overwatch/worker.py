from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from overwatch.config import Settings
from overwatch.models import (
    AgentTrack,
    ChunkPlanItem,
    JobStatus,
    PipelineChunkPlanPayload,
    PipelineProbePayload,
    SourceType,
)
from overwatch.store import JobStore
from overwatch.video import extract_segment_mp4, ffprobe, plan_chunks
from overwatch.vllm_client import (
    chat_completion,
    chunk_video_user_messages,
    extract_assistant_text,
    fetch_models,
)

logger = logging.getLogger(__name__)

_CHUNK_PROMPT = """You are Overwatch video analytics. You are given ONE short MP4 clip cut from a longer recording (warehouse / CCTV style).

Clip time range in the full video: {start_ms} ms → {end_ms} ms (approx. {duration_sec:.1f} s).

Analyse only what is visible in this clip. Answer with these sections (use short bullets):

1) **Main events** — what happens, key actions.
2) **Security / safety** — anything concerning (unauthorized access, PPE, unsafe acts) or "none apparent".
3) **Logistics** — pallets, loads, vehicles, notable object movement, or "none apparent".
4) **Attendance (counts only)** — approximate number of people visible entering/leaving frame or grouped; **no names or identity**.

Be factual; if uncertain, say so."""


async def _extract_chunk_mp4(
    path: Path,
    chunk: ChunkPlanItem,
    settings: Settings,
) -> bytes:
    start_sec = chunk.start_pts_ms / 1000.0
    duration_sec = max(0.01, (chunk.end_pts_ms - chunk.start_pts_ms) / 1000.0)
    use_audio = settings.vllm_segment_include_audio

    async def run(*, max_width: int, crf: int, audio: bool) -> bytes:
        return await extract_segment_mp4(
            path,
            start_sec,
            duration_sec,
            max_width=max_width,
            crf=crf,
            include_audio=audio,
        )

    try:
        mp4 = await run(
            max_width=settings.vllm_video_scale_width,
            crf=settings.vllm_video_crf,
            audio=use_audio,
        )
    except RuntimeError:
        if use_audio:
            logger.warning("Segment extract with audio failed; retrying video-only")
            mp4 = await run(
                max_width=settings.vllm_video_scale_width,
                crf=settings.vllm_video_crf,
                audio=False,
            )
        else:
            raise

    if len(mp4) <= settings.vllm_segment_max_bytes:
        return mp4
    logger.info(
        "Chunk %s segment %d bytes exceeds max; retrying smaller (no audio, lower res)",
        chunk.chunk_index,
        len(mp4),
    )
    return await run(
        max_width=min(320, settings.vllm_video_scale_width),
        crf=min(38, settings.vllm_video_crf + 6),
        audio=False,
    )


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
                f"Overwatch job context (text). A short MP4 clip per planned chunk will be sent next.\n"
                f"source_path={job.source_path}\n"
                f"duration_sec={probe.duration_sec}\n"
                f"avg_frame_rate={probe.avg_frame_rate}\n"
                f"resolution={probe.width}x{probe.height}\n"
                f"codec={probe.codec}\n"
                f"planned_chunks={len(chunks)}\n\n"
                "Reply briefly: confirm you are ready to analyse video clips for this job."
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

            if settings.vllm_multimodal_enabled and settings.vllm_max_chunks_per_job > 0:
                limit = min(settings.vllm_max_chunks_per_job, len(chunks))
                for ch in chunks[:limit]:
                    payload_base: dict = {
                        "chunk_index": ch.chunk_index,
                        "start_pts_ms": ch.start_pts_ms,
                        "end_pts_ms": ch.end_pts_ms,
                        "start_frame": ch.start_frame,
                        "end_frame": ch.end_frame,
                    }
                    try:
                        mp4 = await _extract_chunk_mp4(path, ch, settings)
                        if len(mp4) > settings.vllm_segment_max_bytes:
                            await store.append_event(
                                job.id,
                                agent=AgentTrack.main_events,
                                event_type="vllm_chunk_video",
                                severity="warning",
                                payload={
                                    **payload_base,
                                    "ok": False,
                                    "error": "segment_mp4_too_large",
                                    "bytes": len(mp4),
                                    "max_bytes": settings.vllm_segment_max_bytes,
                                },
                            )
                            continue

                        duration_sec = (ch.end_pts_ms - ch.start_pts_ms) / 1000.0
                        instruction = _CHUNK_PROMPT.format(
                            start_ms=ch.start_pts_ms,
                            end_ms=ch.end_pts_ms,
                            duration_sec=duration_sec,
                        )
                        mm_messages = chunk_video_user_messages(
                            instruction=instruction,
                            mp4_bytes=mp4,
                        )
                        chunk_res = await chat_completion(
                            base,
                            model=settings.vllm_model,
                            messages=mm_messages,
                            api_key=settings.vllm_api_key,
                            timeout_sec=settings.vllm_chunk_timeout_sec,
                            max_tokens=settings.vllm_chunk_max_tokens,
                        )
                        atext = extract_assistant_text(chunk_res.data)
                        nchoices = len(chunk_res.data.get("choices", [])) if chunk_res.data else 0
                        await store.append_event(
                            job.id,
                            agent=AgentTrack.main_events,
                            event_type="vllm_chunk_video",
                            frame_index=ch.start_frame,
                            pts_ms=ch.start_pts_ms,
                            payload={
                                **payload_base,
                                **chunk_res.to_event_payload(
                                    model=settings.vllm_model,
                                    assistant_preview=(
                                        (atext[:4000] + "…") if atext and len(atext) > 4000 else atext
                                    ),
                                    raw_choice_count=nchoices,
                                    segment_bytes=len(mp4),
                                ),
                            },
                        )
                    except Exception as e:
                        logger.exception("Chunk %s multimodal failed", ch.chunk_index)
                        await store.append_event(
                            job.id,
                            agent=AgentTrack.main_events,
                            event_type="vllm_chunk_video",
                            severity="error",
                            frame_index=ch.start_frame,
                            pts_ms=ch.start_pts_ms,
                            payload={**payload_base, "ok": False, "error": str(e)},
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
