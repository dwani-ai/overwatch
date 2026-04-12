from __future__ import annotations

import math

from overwatch.models import ChunkPlanItem
from overwatch.video.probe import VideoProbe


def plan_chunks(
    probe: VideoProbe,
    *,
    target_fps: float = 1.0,
    max_chunk_sec: float = 60.0,
) -> list[ChunkPlanItem]:
    """
    Split the timeline into segments of at most ``max_chunk_sec`` seconds.
    ``target_fps`` is recorded for downstream sampling (Gemma-style ~1 fps windows).
    """
    del target_fps  # reserved for future per-chunk sample lists
    if probe.duration_sec is None or probe.duration_sec <= 0:
        return []

    fps = probe.avg_frame_rate
    if fps is None or fps <= 0:
        fps = 25.0

    duration = probe.duration_sec
    chunks: list[ChunkPlanItem] = []
    t0 = 0.0
    chunk_index = 0
    while t0 < duration - 1e-9:
        t1 = min(duration, t0 + max_chunk_sec)
        start_frame = int(math.floor(t0 * fps))
        end_frame = max(start_frame, int(math.ceil(t1 * fps)) - 1)
        start_pts_ms = int(round(t0 * 1000))
        end_pts_ms = int(round(t1 * 1000))
        chunks.append(
            ChunkPlanItem(
                chunk_index=chunk_index,
                start_frame=start_frame,
                end_frame=end_frame,
                start_pts_ms=start_pts_ms,
                end_pts_ms=end_pts_ms,
            )
        )
        chunk_index += 1
        t0 = t1
    return chunks
