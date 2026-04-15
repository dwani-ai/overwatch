from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from overwatch.analysis.chunk_pipeline import job_summary_from_chunks, run_structured_chunk_analysis
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
from overwatch.vllm_client import chat_completion, extract_assistant_text, fetch_models

if TYPE_CHECKING:
    from overwatch.search.frame_indexer import FrameIndexer
    from overwatch.search.indexer import SearchIndexer

logger = logging.getLogger(__name__)


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


async def _index_frames_background(
    frame_indexer: FrameIndexer,
    store: JobStore,
    job_id: str,
    source_path: str,
    settings: Settings,
) -> None:
    try:
        result = await asyncio.to_thread(
            frame_indexer.index_video_frames,
            job_id,
            source_path,
            settings.frame_sample_fps,
            settings.frame_max_frames_per_job,
        )
        await _store_frame_analysis_events(store, job_id, result)
        logger.info(
            "Frame index: %d frames, %d alerts, %d cuts, %d anomalies for job %s",
            result.get("frame_count", 0),
            len(result.get("visual_alerts", [])),
            len(result.get("scene_changes", [])),
            len(result.get("anomalies", [])),
            job_id,
        )
    except Exception:
        logger.exception("Frame indexing failed for job %s", job_id)


async def _store_frame_analysis_events(
    store: JobStore,
    job_id: str,
    result: dict,
) -> None:
    """Persist frame analysis results as job events in SQLite."""
    # Visual alerts — one event per alert (surfaced prominently)
    for alert in result.get("visual_alerts", []):
        score = alert.get("score", 0.0)
        severity = "high" if score >= 0.35 else "medium"
        await store.append_event(
            job_id,
            agent=AgentTrack.pipeline,
            event_type="visual_alert",
            pts_ms=alert.get("pts_ms"),
            payload=alert,
            severity=severity,
        )

    # Scene changes — single event with full list
    scene_changes = result.get("scene_changes", [])
    if scene_changes:
        await store.append_event(
            job_id,
            agent=AgentTrack.pipeline,
            event_type="scene_changes",
            payload={"changes": scene_changes, "count": len(scene_changes)},
        )

    # Occupancy timeline — single event
    occupancy = result.get("occupancy_timeline", [])
    if occupancy:
        await store.append_event(
            job_id,
            agent=AgentTrack.pipeline,
            event_type="frame_occupancy",
            payload={"timeline": occupancy},
        )

    # Diversity keyframes — single event
    keyframes = result.get("keyframes", [])
    if keyframes:
        await store.append_event(
            job_id,
            agent=AgentTrack.pipeline,
            event_type="frame_keyframes",
            payload={"keyframes": keyframes},
        )

    # Anomalies — single event with list
    anomalies = result.get("anomalies", [])
    if anomalies:
        await store.append_event(
            job_id,
            agent=AgentTrack.pipeline,
            event_type="frame_anomalies",
            payload={"anomalies": anomalies, "count": len(anomalies)},
        )


async def process_one_job(
    store: JobStore,
    settings: Settings,
    indexer: SearchIndexer | None = None,
    frame_indexer: FrameIndexer | None = None,
) -> bool:
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

        chunk_analyses: list[dict] = []

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
                f"Overwatch job context (text). Structured multimodal chunk analysis will follow.\n"
                f"source_path={job.source_path}\n"
                f"duration_sec={probe.duration_sec}\n"
                f"avg_frame_rate={probe.avg_frame_rate}\n"
                f"resolution={probe.width}x{probe.height}\n"
                f"codec={probe.codec}\n"
                f"planned_chunks={len(chunks)}\n\n"
                "Reply briefly: confirm you are ready."
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
                                agent=AgentTrack.pipeline,
                                event_type="chunk_analysis",
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

                        analysis = await run_structured_chunk_analysis(
                            openai_base=base,
                            vllm_model=settings.vllm_model,
                            api_key=settings.vllm_api_key,
                            chunk=ch,
                            mp4_bytes=mp4,
                            settings=settings,
                        )
                        chunk_analyses.append(analysis)
                        await store.append_event(
                            job.id,
                            agent=AgentTrack.pipeline,
                            event_type="chunk_analysis",
                            frame_index=ch.start_frame,
                            pts_ms=ch.start_pts_ms,
                            payload=analysis,
                        )
                        if indexer is not None:
                            try:
                                await asyncio.to_thread(
                                    indexer.index_chunk_analysis,
                                    job.id,
                                    job.source_path,
                                    analysis,
                                )
                            except Exception:
                                logger.warning(
                                    "Search index update failed for chunk %s",
                                    ch.chunk_index,
                                    exc_info=True,
                                )
                    except Exception as e:
                        logger.exception("Chunk %s analysis failed", ch.chunk_index)
                        await store.append_event(
                            job.id,
                            agent=AgentTrack.pipeline,
                            event_type="chunk_analysis",
                            severity="error",
                            frame_index=ch.start_frame,
                            pts_ms=ch.start_pts_ms,
                            payload={**payload_base, "ok": False, "error": str(e)},
                        )

            if chunk_analyses:
                job_summary = job_summary_from_chunks(
                    source_path=job.source_path,
                    duration_sec=probe.duration_sec,
                    planned_chunks=len(chunks),
                    analyses=chunk_analyses,
                )
                await store.set_job_summary(job.id, job_summary)

        await store.update_job_status(job.id, JobStatus.completed)
        if fp:
            await store.record_processed_file(job.source_path, str(fp), job.id)

        # Fire-and-forget frame embedding + analysis after job completes
        if frame_indexer is not None:
            asyncio.create_task(
                _index_frames_background(frame_indexer, store, job.id, str(path), settings)
            )

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


async def worker_loop(
    store: JobStore,
    settings: Settings,
    stop: asyncio.Event,
    indexer: SearchIndexer | None = None,
    frame_indexer: FrameIndexer | None = None,
) -> None:
    while not stop.is_set():
        worked = await process_one_job(
            store, settings, indexer=indexer, frame_indexer=frame_indexer
        )
        if not worked:
            await asyncio.sleep(settings.worker_poll_interval_sec)
