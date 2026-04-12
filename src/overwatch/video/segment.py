from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def extract_segment_mp4(
    src: Path,
    start_sec: float,
    duration_sec: float,
    *,
    max_width: int = 480,
    crf: int = 30,
    include_audio: bool = True,
    audio_bitrate_k: int = 64,
) -> bytes:
    """
    Cut ``duration_sec`` of video starting at ``start_sec`` (seconds), re-encode to
    H.264 MP4 suitable for data-URI upload. Runs ffmpeg; raises on failure.
    """
    if duration_sec <= 0 or start_sec < 0:
        raise ValueError("invalid start_sec or duration_sec")

    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(src),
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        f"scale={max_width}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-movflags",
        "frag_keyframe+empty_moov",
    ]
    if include_audio:
        cmd += ["-c:a", "aac", "-b:a", f"{audio_bitrate_k}k"]
    else:
        cmd.append("-an")
    cmd += ["-f", "mp4", "pipe:1"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("ffmpeg not found")
        raise

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg segment extract failed ({proc.returncode}): {err}")
    if not stdout:
        raise RuntimeError("ffmpeg produced empty segment")
    return stdout
